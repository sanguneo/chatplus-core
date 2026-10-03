# Copyright (c) 2026 chatplus-core contributors
"""Map verified local fields to the tsuba room and message shapes."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal
from urllib.parse import urlsplit

from chatplus_core.hive import decode_frames
from chatplus_core.models import LastMessage, Message, MessagesAnswer, Room, RoomsAnswer
from chatplus_core.storage import Profile, StorageError, StorageIssue, discover, read_stable
from chatplus_core.values import (
    OBJECT_TAG,
    Record,
    Value,
    ValueFormatError,
    ValueIssue,
    decode_value,
)

MAX_LIMIT: Final = 1000
DEFAULT_LIMIT: Final = 50
ROOM_CAP: Final = 1000
PREVIEW_CODE_POINTS: Final = 200
OUTGOING: Final = "Out"
EARLIEST: Final = datetime.min.replace(tzinfo=UTC)

Folder = tuple[str, dict[str | int, Value]]


class InvalidLimitError(ValueError):
    """An invalid read bound."""

    def __init__(self) -> None:
        """Report the public limit contract."""
        super().__init__(f"limit must be between 1 and {MAX_LIMIT}")


class RoomNotFoundError(Exception):
    """No current cached room has the requested opaque ID."""


class MessageNotFoundError(Exception):
    """The `before` id names no cached message in the requested room."""


def mapping(value: Value) -> dict[str | int, Value]:
    """Require an observed map at the storage boundary."""
    if not isinstance(value, dict):
        raise ValueFormatError(ValueIssue.MAP)
    return value


def text(value: Value) -> str:
    """Require a string, without coercing nulls or rich payloads."""
    if not isinstance(value, str):
        raise ValueFormatError(ValueIssue.STRING)
    return value


def optional_text(value: Value) -> str | None:
    """Keep absent text absent."""
    return None if value is None else text(value)


def timestamp(value: Value) -> datetime:
    """Require an ISO 8601 time with an explicit offset."""
    try:
        stamp = datetime.fromisoformat(text(value))
    except ValueError as exc:
        raise ValueFormatError(ValueIssue.DATE) from exc
    if stamp.tzinfo is None:
        raise ValueFormatError(ValueIssue.DATE)
    return stamp


def room_kind(recipients: Value) -> Literal["direct", "group"]:
    """One other participant is a direct chat; several make a group."""
    if recipients is None:
        return "direct"
    if not isinstance(recipients, list):
        raise ValueFormatError(ValueIssue.RECIPIENTS)
    return "group" if len(recipients) > 1 else "direct"


def room_identity(epoch: str, resource_url: str) -> str:
    """Account-scoped room id so identical URLs under different accounts never collide."""
    return hashlib.sha256(f"{epoch}\x1f{resource_url}".encode()).hexdigest()


def records(blob: bytes, profile: Profile, tag: int) -> tuple[Record, ...]:
    """Skip the version sentinel and keys put to null; reject any other unexpected record."""
    result: list[Record] = []
    for key, payload in decode_frames(blob, profile.key).items():
        value = decode_value(payload)
        if value is None or (key == "version" and type(value) is int):
            continue
        if not isinstance(key, str) or not isinstance(value, Record) or value.tag != tag:
            raise ValueFormatError(ValueIssue.RECORD)
        if tag == OBJECT_TAG and value.fields.get(1) != key:
            raise ValueFormatError(ValueIssue.IDENTITY)
        result.append(value)
    return tuple(result)


def to_message(record: Record, room_id: str) -> Message:
    """Map one cached message; a non-text content type is media, without bytes."""
    attrs = mapping(record.fields.get(6))
    content_type = optional_text(attrs.get("Content-Type"))
    textual = content_type is None or content_type.lower().startswith("text/")
    sender = optional_text(attrs.get("From")) or ""
    return Message(
        id=text(record.fields.get(1)),
        room_id=room_id,
        sender=sender,
        sender_id=sender or None,
        text=optional_text(attrs.get("TextContent")) or "",
        kind="text" if textual else "media",
        sent_at=timestamp(attrs.get("Date")),
        from_me=optional_text(attrs.get("Direction")) == OUTGOING,
    )


def to_room(room_id: str, attrs: dict[str | int, Value], newest: Message | None) -> Room:
    """Map one cached conversation, previewing its newest cached message."""
    date = attrs.get("Date")
    if date is not None:
        activity: datetime | None = timestamp(date)
    else:
        activity = newest.sent_at if newest is not None else None
    preview = (
        LastMessage(text=newest.text[:PREVIEW_CODE_POINTS], from_me=newest.from_me)
        if newest is not None
        else None
    )
    return Room(
        id=room_id,
        title=optional_text(attrs.get("Name")) or "",
        kind=room_kind(attrs.get("Recipients")),
        last_activity_at=activity,
        last_message=preview,
    )


def folder_of(parent: str, folders: dict[str, Folder], by_path: dict[str, list[Folder]]) -> Folder:
    """Place a message by its folder's exact URL, else by the one folder at the same path.

    A message sent from this PC can name its folder through another host of the
    same message store; the path (store and folder id) still names one folder.
    No match, or more than one, is refused rather than guessed.
    """
    exact = folders.get(parent)
    if exact is not None:
        return exact
    candidates = by_path.get(urlsplit(parent).path, [])
    if len(candidates) != 1:
        raise StorageError(StorageIssue.ORPHAN)
    return candidates[0]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One observed cache view, not a transactional or complete server history."""

    rooms: tuple[Room, ...]
    messages: tuple[Message, ...]


class Reader:
    """Read current local files per request; never reuse a stale credential key."""

    def __init__(self, profile: Profile | None = None) -> None:
        """Use discovery for live reads, or an explicit profile for synthetic tests."""
        self.profile: Profile | None = profile

    def snapshot(self) -> Snapshot:
        """Fence two stores against observed changes and decode them fail-closed.

        A conversation the client is still creating leaves a placeholder folder and a
        message with no folder link yet; both are skipped as pending, not refused, so
        a send in flight never takes the whole read down.
        """
        profile = self.profile if self.profile is not None else discover()
        paths = (profile.directory / "folder.hive", profile.directory / "object.hive")
        blobs = tuple(read_stable(path) for path in paths)
        if any(read_stable(path) != blob for path, blob in zip(paths, blobs, strict=True)):
            raise StorageError(StorageIssue.CHANGED)
        epoch = profile.directory.name
        folders: dict[str, Folder] = {}
        for folder in records(blobs[0], profile, 37):
            raw = folder.fields.get(0)
            if not isinstance(raw, dict) or "resourceURL" not in raw:
                continue
            url = text(raw.get("resourceURL"))
            if url in folders:
                raise ValueFormatError(ValueIssue.ROOM)
            folders[url] = (room_identity(epoch, url), mapping(folder.fields.get(3)))
        by_path: dict[str, list[Folder]] = {}
        for url, folder in folders.items():
            by_path.setdefault(urlsplit(url).path, []).append(folder)
        messages: list[Message] = []
        for record in records(blobs[1], profile, OBJECT_TAG):
            raw = record.fields.get(0)
            parent = raw.get("parentFolder") if isinstance(raw, dict) else None
            if not isinstance(parent, str) or not parent:
                continue
            messages.append(to_message(record, folder_of(parent, folders, by_path)[0]))
        messages.sort(key=lambda message: (message.sent_at, message.id))
        newest = {message.room_id: message for message in messages}
        rooms = sorted(
            (to_room(room_id, attrs, newest.get(room_id)) for room_id, attrs in folders.values()),
            key=lambda room: room.id,
        )
        rooms.sort(key=lambda room: room.last_activity_at or EARLIEST, reverse=True)
        return Snapshot(rooms=tuple(rooms), messages=tuple(messages))

    def rooms(self) -> RoomsAnswer:
        """Return the rooms latest activity first, up to the adapter's own cap."""
        rooms = self.snapshot().rooms
        return RoomsAnswer(rooms=rooms[:ROOM_CAP], complete=len(rooms) <= ROOM_CAP)

    def messages(
        self,
        room_id: str,
        limit: int = DEFAULT_LIMIT,
        before: str | None = None,
    ) -> MessagesAnswer:
        """Return a room's newest window, or the one just before a message id, oldest first."""
        if not 1 <= limit <= MAX_LIMIT:
            raise InvalidLimitError
        snapshot = self.snapshot()
        if not any(room.id == room_id for room in snapshot.rooms):
            raise RoomNotFoundError
        selected = tuple(message for message in snapshot.messages if message.room_id == room_id)
        pool = selected
        if before is not None:
            index = next((i for i, message in enumerate(selected) if message.id == before), None)
            if index is None:
                raise MessageNotFoundError
            pool = selected[:index]
        page = pool[-limit:]
        return MessagesAnswer(messages=page, has_earlier=len(pool) > len(page))
