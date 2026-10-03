# Copyright (c) 2026 chatplus-core contributors
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient

from chatplus_core.api import (
    Capabilities,
    ConfigurationError,
    Failure,
    create_app,
    stream_events,
)
from chatplus_core.models import Messages, Rooms
from chatplus_core.reader import Reader
from tests.fixtures import profile_at

TOKEN = bytes(range(32)).hex()


@pytest.mark.parametrize("route", ["/capabilities", "/rooms", "/messages", "/openapi.json"])
def test_every_route_requires_authentication(route: str, tmp_path: Path) -> None:
    app = create_app(TOKEN, Reader(profile_at(tmp_path)))
    with TestClient(app) as client:
        response = client.get(route)
    assert response.status_code == 401
    assert Failure.model_validate_json(response.content).error.code == "UNAUTHORIZED"


def test_authenticated_api_reads_real_synthetic_hive(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    room_id = next(
        message.room_id for message in reader.snapshot().messages if message.id == "m-new"
    )
    with TestClient(create_app(TOKEN, reader)) as client:
        response = client.get(
            "/messages",
            params={"roomId": room_id, "limit": 1},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert response.status_code == 200
    assert Messages.model_validate_json(response.content).messages[0].text == "new"


def test_room_limit_reports_truncation(tmp_path: Path) -> None:
    with TestClient(create_app(TOKEN, Reader(profile_at(tmp_path)))) as client:
        response = client.get("/rooms?limit=1", headers={"Authorization": f"Bearer {TOKEN}"})
    payload = Rooms.model_validate_json(response.content)
    assert len(payload.rooms) == 1
    assert payload.total == 2
    assert payload.truncated is True


@pytest.mark.parametrize("query", ["limit=-1", "limit=1001", "limit=no"])
def test_invalid_limits_are_refused(query: str, tmp_path: Path) -> None:
    with TestClient(create_app(TOKEN, Reader(profile_at(tmp_path)))) as client:
        response = client.get(f"/rooms?{query}", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 400
    assert Failure.model_validate_json(response.content).error.code == "BAD_REQUEST"


def test_corrupted_store_is_not_an_empty_success(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)
    _ = (tmp_path / "object.hive").write_bytes(b"corrupt")
    with TestClient(create_app(TOKEN, Reader(profile))) as client:
        response = client.get("/rooms", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503
    assert Failure.model_validate_json(response.content).error.code == "UNSUPPORTED_FORMAT"
    assert str(tmp_path).encode() not in response.content


def test_empty_token_prevents_startup() -> None:
    with pytest.raises(ConfigurationError):
        _ = create_app("")


def test_capabilities_advertise_only_reads(tmp_path: Path) -> None:
    with TestClient(create_app(TOKEN, Reader(profile_at(tmp_path)))) as client:
        response = client.get("/capabilities", headers={"Authorization": f"Bearer {TOKEN}"})
    assert Capabilities.model_validate_json(response.content).features == (
        "rooms.read",
        "messages.read",
        "messages.stream",
    )


def test_missing_room_is_404(tmp_path: Path) -> None:
    with TestClient(create_app(TOKEN, Reader(profile_at(tmp_path)))) as client:
        response = client.get(
            "/messages", params={"roomId": "0" * 64}, headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert response.status_code == 404
    assert Failure.model_validate_json(response.content).error.code == "NOT_FOUND"


def test_send_route_is_absent(tmp_path: Path) -> None:
    with TestClient(create_app(TOKEN, Reader(profile_at(tmp_path)))) as client:
        response = client.post("/send", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 404


def test_specification_matches_validation_status(tmp_path: Path) -> None:
    with TestClient(create_app(TOKEN, Reader(profile_at(tmp_path)))) as client:
        response = client.get("/openapi.json", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert b'"/messages"' in response.content
    assert b'"400"' in response.content
    assert b'"422"' not in response.content
    assert TOKEN.encode() not in response.content


def test_messages_pagination_over_http(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    room_id = next(m.room_id for m in reader.snapshot().messages if m.id == "m-new")
    with TestClient(create_app(TOKEN, reader)) as client:
        first = client.get(
            "/messages",
            params={"roomId": room_id, "limit": 1},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        page_one = Messages.model_validate_json(first.content)
        assert page_one.next_cursor is not None
        second = client.get(
            "/messages",
            params={"roomId": room_id, "limit": 1, "cursor": page_one.next_cursor},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        page_two = Messages.model_validate_json(second.content)
    assert [m.id for m in page_one.messages] == ["m-new"]
    assert [m.id for m in page_two.messages] == ["m-old"]
    assert page_two.next_cursor is None


def test_malformed_cursor_is_bad_request(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    room_id = next(m.room_id for m in reader.snapshot().messages if m.id == "m-new")
    with TestClient(create_app(TOKEN, reader)) as client:
        response = client.get(
            "/messages",
            params={"roomId": room_id, "cursor": "not-a-cursor"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert response.status_code == 400
    assert Failure.model_validate_json(response.content).error.code == "BAD_REQUEST"


def test_stream_requires_auth(tmp_path: Path) -> None:
    with TestClient(create_app(TOKEN, Reader(profile_at(tmp_path)))) as client:
        assert client.get("/stream").status_code == 401


def test_stream_events_emit_ready_frame_first(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))

    async def pull_ready() -> str:
        events = stream_events(reader, [])
        try:
            return await anext(events)
        finally:
            await events.aclose()

    assert "ready" in anyio.run(pull_ready)


def test_messages_expose_attachment_metadata(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    room_id = next(m.room_id for m in reader.snapshot().messages if m.id == "m-img")
    with TestClient(create_app(TOKEN, reader)) as client:
        response = client.get(
            "/messages",
            params={"roomId": room_id, "limit": 10},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    payload = Messages.model_validate_json(response.content)
    image = next(m for m in payload.messages if m.id == "m-img")
    assert image.content_type == "image/jpeg"
    assert image.has_attachment is True


def test_media_byte_route_is_absent(tmp_path: Path) -> None:
    with TestClient(create_app(TOKEN, Reader(profile_at(tmp_path)))) as client:
        response = client.get("/media", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 404
