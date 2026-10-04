# Copyright (c) 2026 chatplus-core contributors
from datetime import UTC, datetime
from pathlib import Path
from typing import final

import anyio
import httpx2
import pytest
from fastapi.testclient import TestClient
from typing_extensions import override

from chatplus_core import upload as upload_module
from chatplus_core.api import create_app
from chatplus_core.models import Message, Room
from chatplus_core.reader import Reader, Snapshot
from chatplus_core.sender import RoomUnverifiedError, SendError
from chatplus_core.storage import StorageError, StorageIssue
from chatplus_core.upload import UPLOAD_MAX_BYTES
from tests.wire import arr, error_code, obj, parse

TOKEN = bytes(range(32)).hex()
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MOBILE = Room(id="r-1", title="친구", kind="direct")
GROUP = Room(id="r-2", title="모임", kind="group")
NUMBER = "01000000000"
PICTURE = b"\x89PNG synthetic"


def msg(mid: str, *, from_me: bool, kind: str = "text") -> Message:
    return Message.model_validate(
        {
            "id": mid,
            "roomId": "r-1",
            "sender": "s",
            "text": "",
            "kind": kind,
            "sentAt": datetime(2026, 1, 1, tzinfo=UTC),
            "fromMe": from_me,
        }
    )


@final
class Store(Reader):
    def __init__(self, messages: list[Message], *, flaky: int = 0) -> None:
        super().__init__(None)
        self.messages_data: list[Message] = messages
        self.reads: int = 0
        self.flaky: int = flaky

    @override
    def snapshot(self) -> Snapshot:
        self.reads += 1
        if self.reads == self.flaky:
            raise StorageError(StorageIssue.CHANGED)
        return Snapshot(rooms=(MOBILE, GROUP), messages=tuple(self.messages_data))

    @override
    def recipient_numbers(self) -> dict[str, str | None]:
        return {"r-1": NUMBER, "r-2": None}


@final
class EchoUploader:
    def __init__(self, store: Store) -> None:
        self.store: Store = store
        self.calls: list[tuple[str, bytes, str]] = []

    async def upload(self, number: str, picture: Path) -> None:
        self.calls.append((number, await anyio.Path(picture).read_bytes(), picture.name))
        self.store.messages_data.append(msg("sent", from_me=True, kind="media"))


@final
class QuietUploader:
    def __init__(self, error: SendError | None = None) -> None:
        self.error: SendError | None = error
        self.calls: int = 0

    async def upload(self, number: str, picture: Path) -> None:
        _ = (number, picture)
        self.calls += 1
        if self.error is not None:
            raise self.error


def post(
    client: TestClient, data: dict[str, str], name: str = "a.png", kind: str = "image/png"
) -> httpx2.Response:
    return client.post("/upload", data=data, files={"file": (name, PICTURE, kind)}, headers=AUTH)


def test_upload_is_not_supported_without_an_uploader() -> None:
    with TestClient(create_app(TOKEN, Store([]))) as client:
        features = obj(obj(parse(client.get("/capabilities", headers=AUTH).content))["features"])
        response = post(client, {"roomId": "r-1"})
    assert "attachments.send" not in features
    assert response.status_code == 501
    assert error_code(response.content) == "NOT_SUPPORTED"


def test_a_wired_uploader_declares_pictures_only_and_answers_the_stored_message(
    tmp_path: Path,
) -> None:
    store = Store([msg("old", from_me=True)])
    uploader = EchoUploader(store)
    with TestClient(create_app(TOKEN, store, uploader=uploader, staging=tmp_path)) as client:
        features = obj(obj(parse(client.get("/capabilities", headers=AUTH).content))["features"])
        response = post(client, {"roomId": "r-1"}, name="../a.png")
    assert features["attachments.send"] == {
        "maxBytes": UPLOAD_MAX_BYTES,
        "caption": False,
        "types": ["image/jpeg", "image/png", "image/gif"],
        "receipt": "stored",
    }
    assert response.status_code == 200
    message = obj(obj(parse(response.content))["message"])
    assert (message["id"], message["kind"], message["fromMe"]) == ("sent", "media", True)
    assert uploader.calls == [(NUMBER, PICTURE, "a.png")]
    assert [path.parent.parent for path in tmp_path.glob("*/a.png")] == [tmp_path]


def test_rooms_without_one_mobile_number_take_no_pictures(tmp_path: Path) -> None:
    store = Store([])
    app = create_app(TOKEN, store, uploader=EchoUploader(store), staging=tmp_path)
    with TestClient(app) as client:
        rooms = arr(obj(parse(client.get("/rooms", headers=AUTH).content))["rooms"])
    by_id = {obj(room)["id"]: obj(room) for room in rooms}
    assert "features" not in by_id["r-1"]
    assert by_id["r-2"]["features"] == {"attachments.send": False}


@pytest.mark.parametrize(
    ("data", "name", "kind", "status", "code"),
    [
        ({"roomId": "r-1"}, "a.txt", "text/plain", 400, "INVALID_REQUEST"),
        ({"roomId": "r-1", "caption": "hi"}, "a.png", "image/png", 400, "INVALID_REQUEST"),
        ({"roomId": "r-1", "replyTo": "m"}, "a.png", "image/png", 501, "NOT_SUPPORTED"),
        ({"roomId": "r-1", "other": "x"}, "a.png", "image/png", 400, "INVALID_REQUEST"),
        ({}, "a.png", "image/png", 400, "INVALID_REQUEST"),
        ({"roomId": "r-2"}, "a.png", "image/png", 403, "FORBIDDEN"),
        ({"roomId": "r-9"}, "a.png", "image/png", 404, "NOT_FOUND"),
    ],
)
def test_wrong_forms_and_rooms_are_refused_and_send_nothing(  # noqa: PLR0913, PLR0917
    tmp_path: Path, data: dict[str, str], name: str, kind: str, status: int, code: str
) -> None:
    uploader = QuietUploader()
    with TestClient(create_app(TOKEN, Store([]), uploader=uploader, staging=tmp_path)) as client:
        response = post(client, data, name=name, kind=kind)
    assert response.status_code == status
    assert error_code(response.content) == code
    assert uploader.calls == 0


def test_a_file_past_the_bound_is_refused_and_sends_nothing(tmp_path: Path) -> None:
    uploader = QuietUploader()
    big = b"0" * (UPLOAD_MAX_BYTES + 1)
    with TestClient(create_app(TOKEN, Store([]), uploader=uploader, staging=tmp_path)) as client:
        response = client.post(
            "/upload",
            data={"roomId": "r-1"},
            files={"file": ("a.png", big, "image/png")},
            headers=AUTH,
        )
        empty = client.post(
            "/upload",
            data={"roomId": "r-1"},
            files={"file": ("a.png", b"", "image/png")},
            headers=AUTH,
        )
    assert (response.status_code, error_code(response.content)) == (400, "INVALID_REQUEST")
    assert (empty.status_code, error_code(empty.content)) == (400, "INVALID_REQUEST")
    assert uploader.calls == 0


def test_a_refusal_keeps_its_code_and_an_unconfirmed_picture_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_module, "UPLOAD_CONFIRM_SECONDS", 0.05)
    refusing = QuietUploader(RoomUnverifiedError())
    silent = QuietUploader()
    with TestClient(create_app(TOKEN, Store([]), uploader=refusing, staging=tmp_path)) as client:
        refused = post(client, {"roomId": "r-1"})
    with TestClient(create_app(TOKEN, Store([]), uploader=silent, staging=tmp_path)) as client:
        unknown = post(client, {"roomId": "r-1"})
    assert (refused.status_code, error_code(refused.content)) == (403, "FORBIDDEN")
    assert (unknown.status_code, error_code(unknown.content)) == (500, "INTERNAL")
    assert (refusing.calls, silent.calls) == (1, 1)


def test_the_read_back_reads_again_while_the_client_is_writing(tmp_path: Path) -> None:
    store = Store([], flaky=2)
    uploader = EchoUploader(store)
    with TestClient(create_app(TOKEN, store, uploader=uploader, staging=tmp_path)) as client:
        response = post(client, {"roomId": "r-1"})
    assert response.status_code == 200
    assert store.reads >= 3
