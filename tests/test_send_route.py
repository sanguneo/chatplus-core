# Copyright (c) 2026 chatplus-core contributors
from datetime import UTC, datetime
from typing import final

import anyio
import pytest
from fastapi.testclient import TestClient
from typing_extensions import override

from chatplus_core import api as api_module
from chatplus_core.api import create_app, perform_send
from chatplus_core.models import Message, Room, SendRequest
from chatplus_core.reader import Reader, Snapshot
from chatplus_core.sender import (
    ClientUnavailableError,
    RoomUnverifiedError,
    WriteLine,
    WriteLineFullError,
    confirm,
)
from tests.wire import error_code, obj, parse

TOKEN = bytes(range(32)).hex()
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ROOM = Room(id="r-1", title="친구", kind="direct")


def msg(mid: str, text: str, *, from_me: bool, room: str = "r-1") -> Message:
    return Message(
        id=mid,
        room_id=room,
        sender="s",
        text=text,
        kind="text",
        sent_at=datetime(2026, 1, 1, tzinfo=UTC),
        from_me=from_me,
    )


@final
class Store(Reader):
    def __init__(self, rooms: list[Room], messages: list[Message]) -> None:
        super().__init__(None)
        self.rooms_data: list[Room] = rooms
        self.messages_data: list[Message] = messages

    @override
    def snapshot(self) -> Snapshot:
        return Snapshot(rooms=tuple(self.rooms_data), messages=tuple(self.messages_data))


@final
class EchoSender:
    def __init__(self, store: Store) -> None:
        self.store: Store = store
        self.calls: list[tuple[str, str]] = []

    async def submit(self, room: Room, text: str) -> None:
        self.calls.append((room.id, text))
        self.store.messages_data.append(
            msg(f"sent-{len(self.store.messages_data)}", text, from_me=True, room=room.id)
        )


@final
class SilentSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def submit(self, room: Room, text: str) -> None:
        self.calls.append((room.id, text))


@final
class RefusingSender:
    def __init__(self, error: Exception) -> None:
        self.error: Exception = error

    async def submit(self, room: Room, text: str) -> None:
        _ = (room, text)
        raise self.error


def test_send_is_not_supported_without_a_sender() -> None:
    store = Store([ROOM], [])
    with TestClient(create_app(TOKEN, store)) as c:
        body = obj(parse(c.get("/capabilities", headers=AUTH).content))
        response = c.post("/send", json={"roomId": "r-1", "text": "hi"}, headers=AUTH)
    assert "messages.send" not in obj(body["features"])
    assert response.status_code == 501
    assert error_code(response.content) == "NOT_SUPPORTED"


def test_a_wired_sender_declares_the_feature_and_sends() -> None:
    store = Store([ROOM], [msg("old", "earlier", from_me=False)])
    sender = EchoSender(store)
    with TestClient(create_app(TOKEN, store, sender)) as c:
        features = obj(obj(parse(c.get("/capabilities", headers=AUTH).content))["features"])
        response = c.post("/send", json={"roomId": "r-1", "text": "hello"}, headers=AUTH)
    assert features["messages.send"] == {"maxLength": 1000, "receipt": "stored"}
    assert response.status_code == 200
    message = obj(obj(parse(response.content))["message"])
    assert (message["text"], message["fromMe"], message["roomId"]) == ("hello", True, "r-1")
    assert sender.calls == [("r-1", "hello")]


def test_reply_to_is_not_supported() -> None:
    store = Store([ROOM], [])
    with TestClient(create_app(TOKEN, store, EchoSender(store))) as c:
        response = c.post(
            "/send", json={"roomId": "r-1", "text": "hi", "replyTo": "m-9"}, headers=AUTH
        )
    assert response.status_code == 501
    assert error_code(response.content) == "NOT_SUPPORTED"


@pytest.mark.parametrize("text", ["", "x" * 1001])
def test_text_outside_the_length_bound_is_refused(text: str) -> None:
    store = Store([ROOM], [])
    with TestClient(create_app(TOKEN, store, EchoSender(store))) as c:
        response = c.post("/send", json={"roomId": "r-1", "text": text}, headers=AUTH)
    assert response.status_code == 400
    assert error_code(response.content) == "INVALID_REQUEST"


def test_sending_to_an_unknown_room_is_not_found() -> None:
    store = Store([ROOM], [])
    sender = EchoSender(store)
    with TestClient(create_app(TOKEN, store, sender)) as c:
        response = c.post("/send", json={"roomId": "nope", "text": "hi"}, headers=AUTH)
    assert response.status_code == 404
    assert error_code(response.content) == "NOT_FOUND"
    assert sender.calls == []


def test_a_submitted_send_that_cannot_be_confirmed_is_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_module, "SEND_CONFIRM_SECONDS", 0.0)
    store = Store([ROOM], [])
    sender = SilentSender()
    with TestClient(create_app(TOKEN, store, sender)) as c:
        response = c.post("/send", json={"roomId": "r-1", "text": "hi"}, headers=AUTH)
    assert response.status_code == 500
    assert error_code(response.content) == "INTERNAL"
    assert sender.calls == [("r-1", "hi")]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ClientUnavailableError(), 503, "REAUTH_REQUIRED"),
        (RoomUnverifiedError(), 403, "FORBIDDEN"),
    ],
)
def test_a_refused_send_maps_to_its_code(error: Exception, status: int, code: str) -> None:
    store = Store([ROOM], [])
    with TestClient(create_app(TOKEN, store, RefusingSender(error))) as c:
        response = c.post("/send", json={"roomId": "r-1", "text": "hi"}, headers=AUTH)
    assert response.status_code == status
    assert error_code(response.content) == code


def test_a_full_write_line_refuses_the_send() -> None:
    store = Store([ROOM], [])

    async def run() -> int:
        body = SendRequest(room_id="r-1", text="hi")
        response = await perform_send(store, EchoSender(store), WriteLine(capacity=0), body)
        return response.status_code

    assert anyio.run(run) == 429


def test_write_line_refuses_past_its_capacity() -> None:
    async def run() -> bool:
        line = WriteLine(capacity=1)
        started = anyio.Event()
        release = anyio.Event()
        refused = False

        async def hold() -> None:
            async with line.admit():
                started.set()
                await release.wait()

        async with anyio.create_task_group() as tg:
            _ = tg.start_soon(hold)
            await started.wait()
            try:
                async with line.admit():
                    pass
            except WriteLineFullError:
                refused = True
            release.set()
        return refused

    assert anyio.run(run) is True


def test_write_line_runs_one_at_a_time() -> None:
    async def run() -> list[str]:
        line = WriteLine(capacity=4)
        order: list[str] = []
        first_in = anyio.Event()
        first_go = anyio.Event()

        async def first() -> None:
            async with line.admit():
                order.append("a-start")
                first_in.set()
                await first_go.wait()
                order.append("a-end")

        async def second() -> None:
            await first_in.wait()
            async with line.admit():
                order.append("b-start")

        async with anyio.create_task_group() as tg:
            _ = tg.start_soon(first)
            _ = tg.start_soon(second)
            await first_in.wait()
            first_go.set()
        return order

    assert anyio.run(run) == ["a-start", "a-end", "b-start"]


def test_confirm_returns_the_new_outgoing_message() -> None:
    store = Store([ROOM], [msg("old", "hi", from_me=True), msg("in", "hi", from_me=False)])
    store.messages_data.append(msg("new", "hi", from_me=True))

    async def run() -> str | None:
        found = await confirm(store, "r-1", "hi", frozenset({"old"}), deadline_seconds=1.0)
        return found.id if found is not None else None

    assert anyio.run(run) == "new"


def test_confirm_times_out_without_a_match() -> None:
    store = Store([ROOM], [msg("in", "hi", from_me=False)])

    async def run() -> Message | None:
        return await confirm(store, "r-1", "hi", frozenset(), deadline_seconds=0.0)

    assert anyio.run(run) is None
