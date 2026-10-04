# Copyright (c) 2026 chatplus-core contributors
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from typing_extensions import override

from chatplus_core.api import (
    ConfigurationError,
    StreamSlots,
    create_app,
    held,
    open_stream,
    stream_events,
)
from chatplus_core.models import MessagesAnswer, RoomsAnswer
from chatplus_core.reader import Reader, Snapshot
from chatplus_core.storage import StorageError, StorageIssue
from chatplus_core.stream import initial_positions
from chatplus_core.values import Record
from tests.fixtures import encode, profile_at
from tests.test_hive import frame
from tests.wire import arr, error_code, obj, parse, sse

TOKEN = bytes(range(32)).hex()
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def reader(tmp_path: Path) -> Reader:
    return Reader(profile_at(tmp_path))


@pytest.fixture
def client(reader: Reader) -> Iterator[TestClient]:
    with TestClient(create_app(TOKEN, reader)) as test_client:
        yield test_client


def room_of(reader: Reader, message_id: str) -> str:
    return next(m.room_id for m in reader.snapshot().messages if m.id == message_id)


class UnreadableReader(Reader):
    @override
    def snapshot(self) -> Snapshot:
        raise StorageError(StorageIssue.CHANGED)


def test_health_answers_without_a_token(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert parse(response.content) == {"ok": True}


def test_wrong_token_is_unauthorized_with_a_challenge(client: TestClient) -> None:
    response = client.get("/rooms", headers={"Authorization": "Bearer " + "x" * 64})
    assert response.status_code == 401
    assert error_code(response.content) == "UNAUTHORIZED"
    assert response.headers["www-authenticate"] == "Bearer"


def test_empty_token_prevents_startup() -> None:
    with pytest.raises(ConfigurationError):
        _ = create_app("")


def test_capabilities_declare_only_reads(client: TestClient) -> None:
    body = obj(parse(client.get("/capabilities", headers=AUTH).content))
    features = obj(body["features"])
    assert set(features) == {"rooms.list", "messages.read", "messages.stream"}
    assert features["messages.read"] == {"paging": "before-id", "maxLimit": 1000}
    assert features["messages.stream"] == {"resume": "cursor"}
    assert body["account"] == {"kind": "user"}


def test_rooms_come_latest_activity_first_with_their_kind(client: TestClient) -> None:
    payload = RoomsAnswer.model_validate_json(client.get("/rooms", headers=AUTH).content)
    assert payload.complete is True
    assert [room.kind for room in payload.rooms] == ["group", "direct"]


def test_room_and_message_wire_keys(client: TestClient, reader: Reader) -> None:
    rooms = parse(client.get("/rooms", headers=AUTH).content)
    params = {"roomId": room_of(reader, "m-new")}
    messages = parse(client.get("/messages", params=params, headers=AUTH).content)
    room = obj(arr(obj(rooms)["rooms"])[0])
    message = obj(arr(obj(messages)["messages"])[0])
    assert set(room) == {"id", "title", "kind", "unread", "lastActivityAt", "lastMessage"}
    assert set(message) == {
        "id",
        "roomId",
        "sender",
        "senderId",
        "text",
        "kind",
        "sentAt",
        "fromMe",
    }


def test_a_key_that_does_not_apply_is_absent_not_null(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)
    anonymous = Record(
        38,
        {
            0: {"parentFolder": "https://synthetic.invalid/room/a"},
            1: "m-anon",
            6: {"Date": "2026-01-01T05:00:00Z", "TextContent": "hi", "Direction": "In"},
        },
    )
    with (tmp_path / "object.hive").open("ab") as output:
        _ = output.write(frame("m-anon", encode(anonymous)))
    reader = Reader(profile)
    with TestClient(create_app(TOKEN, reader)) as client:
        params = {"roomId": room_of(reader, "m-anon")}
        response = client.get("/messages", params=params, headers=AUTH)
    newest = obj(arr(obj(parse(response.content))["messages"])[-1])
    assert newest["sender"] == ""
    assert "senderId" not in newest
    assert b"null" not in response.content


def test_messages_read_the_newest_window(client: TestClient, reader: Reader) -> None:
    params = {"roomId": room_of(reader, "m-new"), "limit": "1"}
    response = client.get("/messages", params=params, headers=AUTH)
    payload = MessagesAnswer.model_validate_json(response.content)
    assert [m.id for m in payload.messages] == ["m-new"]
    assert payload.has_earlier is True


def test_before_pages_backward_over_http(client: TestClient, reader: Reader) -> None:
    params = {"roomId": room_of(reader, "m-new"), "limit": "1", "before": "m-new"}
    response = client.get("/messages", params=params, headers=AUTH)
    payload = MessagesAnswer.model_validate_json(response.content)
    assert [m.id for m in payload.messages] == ["m-old"]
    assert payload.has_earlier is False


@pytest.mark.parametrize(
    "params",
    [
        {"roomId": "r", "limit": "0"},
        {"roomId": "r", "limit": "1001"},
        {"roomId": "r", "limit": "no"},
        {},
    ],
)
def test_invalid_reads_are_refused(client: TestClient, params: dict[str, str]) -> None:
    response = client.get("/messages", params=params, headers=AUTH)
    assert response.status_code == 400
    assert error_code(response.content) == "INVALID_REQUEST"


def test_missing_room_is_not_found(client: TestClient) -> None:
    response = client.get("/messages", params={"roomId": "0" * 64}, headers=AUTH)
    assert response.status_code == 404
    assert error_code(response.content) == "NOT_FOUND"


def test_unknown_before_is_not_found(client: TestClient, reader: Reader) -> None:
    params = {"roomId": room_of(reader, "m-new"), "before": "gone"}
    response = client.get("/messages", params=params, headers=AUTH)
    assert response.status_code == 404
    assert error_code(response.content) == "NOT_FOUND"


def test_corrupted_store_is_internal_without_paths(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)
    _ = (tmp_path / "object.hive").write_bytes(b"corrupt")
    with TestClient(create_app(TOKEN, Reader(profile))) as client:
        response = client.get("/rooms", headers=AUTH)
    assert response.status_code == 500
    assert error_code(response.content) == "INTERNAL"
    assert str(tmp_path).encode() not in response.content


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/media"),
        ("POST", "/send"),
        ("POST", "/edit"),
        ("POST", "/delete"),
        ("POST", "/react"),
        ("POST", "/read"),
    ],
)
def test_undeclared_features_answer_not_supported(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path, headers=AUTH)
    assert response.status_code == 501
    assert error_code(response.content) == "NOT_SUPPORTED"


def test_icon_is_served_as_the_provider_icon(client: TestClient) -> None:
    response = client.get("/icon", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_wrong_method_names_the_allowed_one(client: TestClient) -> None:
    response = client.get("/send", headers=AUTH)
    assert response.status_code == 405
    assert error_code(response.content) == "METHOD_NOT_ALLOWED"
    assert "POST" in response.headers["allow"]


def test_unknown_route_is_not_found(client: TestClient) -> None:
    response = client.get("/nowhere", headers=AUTH)
    assert response.status_code == 404
    assert error_code(response.content) == "NOT_FOUND"


def test_stream_refuses_a_cursor_it_cannot_honour(client: TestClient, reader: Reader) -> None:
    params = {"since": f"{room_of(reader, 'm-new')}:gone"}
    response = client.get("/stream", params=params, headers=AUTH)
    assert response.status_code == 400
    assert error_code(response.content) == "INVALID_REQUEST"


def test_stream_is_busy_past_its_limit(reader: Reader) -> None:
    async def admit() -> tuple[int, bytes]:
        response = await open_stream(reader, StreamSlots(limit=1, live=1), [])
        return response.status_code, bytes(response.body)

    status, body = anyio.run(admit)
    assert status == 503
    assert error_code(body) == "BUSY"


def test_a_stream_holds_its_slot_until_it_ends() -> None:
    slots = StreamSlots(limit=8)

    async def source() -> AsyncGenerator[str, None]:
        yield "frame"

    async def run() -> tuple[int, int, int]:
        before = slots.live
        events = held(slots, source())
        _ = await anext(events)
        during = slots.live
        await events.aclose()
        return before, during, slots.live

    assert anyio.run(run) == (0, 1, 0)


def test_stream_resumes_a_named_room_then_goes_live(reader: Reader) -> None:
    snapshot = reader.snapshot()
    room, other = room_of(reader, "m-new"), room_of(reader, "m-img")

    async def pull() -> list[str]:
        positions = initial_positions(snapshot, {room: "m-old"})
        events = stream_events(reader, snapshot, positions, resumed=True, poll_seconds=0)
        try:
            return [await anext(events) for _ in range(3)]
        finally:
            await events.aclose()

    ready, message, cursor = (sse(frame) for frame in anyio.run(pull))
    assert ready == (
        "ready",
        {"ok": True, "resumed": True, "cursor": {room: "m-old", other: "m-img"}},
    )
    assert message[0] == "message"
    assert obj(obj(message[1])["message"])["id"] == "m-new"
    assert cursor == ("cursor", {"ok": True, "cursor": {room: "m-new"}})


def test_a_failed_read_ends_the_stream_with_an_error_event(reader: Reader) -> None:
    snapshot = reader.snapshot()

    async def pull() -> list[str]:
        events = stream_events(
            UnreadableReader(),
            snapshot,
            initial_positions(snapshot, {}),
            resumed=False,
            poll_seconds=0,
        )
        return [frame async for frame in events]

    frames = [sse(frame) for frame in anyio.run(pull)]
    assert [name for name, _ in frames] == ["ready", "error"]
    error = obj(frames[1][1])
    assert error["ok"] is False
    assert obj(error["error"])["code"] == "INTERNAL"
