# Copyright (c) 2026 chatplus-core contributors
"""POST /upload: one picture put into a room through the client's own window.

What stays here is independent of the desktop: the form, its bounds, staging the
bytes where the client's open-file dialog can read them, the shared write line,
and the read-back that turns a submitted picture into the message the client
stored. The window driver is the `Uploader` seam. A picture the read-back cannot
find is uncertain, answered INTERNAL, and never retried.
"""

import secrets
import shutil
import time
from collections.abc import AsyncGenerator
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

import anyio
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from chatplus_core.envelope import ErrorCode, failure
from chatplus_core.models import Message, SendAnswer
from chatplus_core.reader import Reader
from chatplus_core.sender import SendError, WriteLine
from chatplus_core.storage import StorageError
from chatplus_core.values import ValueFormatError

# The adapter's own bound: the client re-encodes pictures before sending them,
# so the messenger's limit applies to what it makes, not to what is given here.
UPLOAD_MAX_BYTES: Final = 10 * 1024 * 1024
# Pictures only: the room's composer shows these inline and sends them as one.
UPLOAD_TYPES: Final = ("image/jpeg", "image/png", "image/gif")
UPLOAD_FEATURE: Final[dict[str, str | int | bool | list[str]]] = {
    "maxBytes": UPLOAD_MAX_BYTES,
    "caption": False,
    "types": list(UPLOAD_TYPES),
    "receipt": "stored",
}
FORM_OVERHEAD: Final = 64 * 1024
FORM_PARTS: Final = frozenset({"roomId", "file", "caption", "replyTo"})
MAX_ID_LENGTH: Final = 512
MAX_NAME_LENGTH: Final = 120
UPLOAD_CONFIRM_SECONDS: Final = 30.0
CONFIRM_POLL_SECONDS: Final = 0.5
UPLOAD_KEEP_SECONDS: Final = 3600.0
# A store the client is writing reads as changed or half-written; read again.
TRANSIENT: Final = (StorageError, ValueFormatError, OSError)


class Uploader(Protocol):
    """Drive the running client to send one picture to one mobile number's conversation."""

    async def upload(self, number: str, picture: Path) -> None:
        """Submit the picture there, or raise a SendError having sent nothing."""
        ...


class FormTooLargeError(Exception):
    """The body ran past the bound; nothing was read whole and nothing sent."""


def too_large() -> Response:
    """Refuse a file past maxBytes before it is read whole."""
    return failure(ErrorCode.INVALID_REQUEST, Wrong.SIZE)


async def bounded(request: Request, limit: int) -> AsyncGenerator[bytes]:
    """Pass the body on, stopping it at the bound whatever its length said."""
    seen = 0
    async for chunk in request.stream():
        seen += len(chunk)
        if seen > limit:
            raise FormTooLargeError
        yield chunk


async def read_form(request: Request) -> FormData | Response:
    """Parse one multipart form no larger than the picture bound plus its parts."""
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        return failure(ErrorCode.INVALID_REQUEST, "The body must be multipart/form-data")
    limit = UPLOAD_MAX_BYTES + FORM_OVERHEAD
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        return too_large()
    parser = MultiPartParser(
        request.headers, bounded(request, limit), max_files=1, max_fields=len(FORM_PARTS)
    )
    try:
        return await parser.parse()
    except FormTooLargeError:
        return too_large()
    except MultiPartException:
        return failure(ErrorCode.INVALID_REQUEST, "The form could not be read")


def media_type(file: UploadFile) -> str:
    """Return the part's type without parameters, lowercase; empty when the part names none."""
    return (file.content_type or "").split(";")[0].strip().lower()


def safe_name(name: str | None) -> str:
    """Return a file name the dialog can take: no directories, no reserved characters."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join("_" if ch in '<>:"|?*' or not ch.isprintable() else ch for ch in base).strip(
        " ."
    )
    return cleaned[:MAX_NAME_LENGTH] or "picture"


def sweep(directory: Path, now: float) -> None:
    """Remove pictures staged more than an hour ago; the client has long read them."""
    if not directory.is_dir():
        return
    for entry in directory.iterdir():
        try:
            if now - entry.stat().st_mtime > UPLOAD_KEEP_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def stage(directory: Path, name: str, data: bytes) -> Path:
    """Write the picture under a fresh folder so its name reaches the client as given."""
    sweep(directory, time.time())
    folder = directory / secrets.token_hex(8)
    folder.mkdir(parents=True)
    path = folder / safe_name(name)
    _ = path.write_bytes(data)
    return path


async def confirm_media(
    reader: Reader, room_id: str, before: frozenset[str], *, deadline_seconds: float
) -> Message | None:
    """Return the picture message the client stored for a submitted upload, or None.

    A match is a newly cached outgoing media message of this room that was not
    there before. No match by the deadline means the outcome is unknown.
    """
    with anyio.move_on_after(deadline_seconds):
        while True:
            try:
                snapshot = await anyio.to_thread.run_sync(reader.snapshot)
            except TRANSIENT:
                await anyio.sleep(CONFIRM_POLL_SECONDS)
                continue
            found = next(
                (
                    message
                    for message in reversed(snapshot.messages)
                    if message.room_id == room_id
                    and message.from_me
                    and message.kind == "media"
                    and message.id not in before
                ),
                None,
            )
            if found is not None:
                return found
            await anyio.sleep(CONFIRM_POLL_SECONDS)
    return None


class Wrong(StrEnum):
    """Each way a form can break the contract, as the client reads it."""

    PARTS = "Only roomId, file, caption and replyTo, once"
    ROOM = "roomId must be a room id"
    FILE = "file must be one file"
    SIZE = f"file must be 1 to {UPLOAD_MAX_BYTES} bytes"
    TYPE = f"file must be {', '.join(UPLOAD_TYPES)}"
    CAPTION = "This adapter sends pictures without captions"
    REPLY = "messages.reply is not declared"


class FormRefusalError(Exception):
    """A form the contract does not allow, refused before any client touch."""

    def __init__(self, wrong: Wrong) -> None:
        """Carry the code and the reason the client sees."""
        self.code: ErrorCode = (
            ErrorCode.NOT_SUPPORTED if wrong is Wrong.REPLY else ErrorCode.INVALID_REQUEST
        )
        self.message: str = str(wrong)
        super().__init__(self.message)


def checked(form: FormData) -> tuple[str, UploadFile]:
    """Validate the parts as the contract states them, or raise FormRefusalError."""
    names = [name for name, _ in form.multi_items()]
    if any(name not in FORM_PARTS for name in names) or len(names) != len(set(names)):
        raise FormRefusalError(Wrong.PARTS)
    room_id = form.get("roomId")
    if not isinstance(room_id, str) or not 1 <= len(room_id) <= MAX_ID_LENGTH:
        raise FormRefusalError(Wrong.ROOM)
    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise FormRefusalError(Wrong.FILE)
    if not 1 <= (file.size or 0) <= UPLOAD_MAX_BYTES:
        raise FormRefusalError(Wrong.SIZE)
    if media_type(file) not in UPLOAD_TYPES:
        raise FormRefusalError(Wrong.TYPE)
    if "caption" in form:
        raise FormRefusalError(Wrong.CAPTION)
    if "replyTo" in form:
        raise FormRefusalError(Wrong.REPLY)
    return room_id, file


async def perform_upload(  # noqa: PLR0911 - each refusal is its own documented answer
    reader: Reader, uploader: Uploader, line: WriteLine, request: Request, staging: Path
) -> Response:
    """Send one picture into an existing mobile room, answering the message the client stored."""
    form = await read_form(request)
    if isinstance(form, Response):
        return form
    try:
        room_id, file = checked(form)
        data = await file.read()
    except FormRefusalError as wrong:
        return failure(wrong.code, wrong.message)
    finally:
        await form.close()
    snapshot = await anyio.to_thread.run_sync(reader.snapshot)
    if not any(room.id == room_id for room in snapshot.rooms):
        return failure(ErrorCode.NOT_FOUND, "No such room in the local cache")
    number = (await anyio.to_thread.run_sync(reader.recipient_numbers)).get(room_id)
    if number is None:
        return failure(
            ErrorCode.FORBIDDEN, "Only a room with one mobile number takes pictures here"
        )
    before = frozenset(m.id for m in snapshot.messages if m.room_id == room_id and m.from_me)
    picture = await anyio.to_thread.run_sync(stage, staging, file.filename or "", data)
    try:
        async with line.admit():
            await uploader.upload(number, picture)
            stored = await confirm_media(
                reader, room_id, before, deadline_seconds=UPLOAD_CONFIRM_SECONDS
            )
    except SendError as refusal:
        return failure(refusal.code, refusal.message)
    if stored is None:
        return failure(ErrorCode.INTERNAL, "submitted but not confirmed")
    answer = SendAnswer(message=stored)
    return JSONResponse(
        content=answer.model_dump(by_alias=True, exclude_none=True, mode="json"),
        headers={"cache-control": "no-store"},
    )
