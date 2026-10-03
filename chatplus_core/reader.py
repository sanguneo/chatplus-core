# Copyright (c) 2026 chatplus-core contributors
"""Map verified local fields to a bounded, explicitly cache-only API."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from chatplus_core.hive import decode_frames
from chatplus_core.models import Message, Messages, Room, Rooms
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


class InvalidLimitError(ValueError):
    """An invalid read bound."""

    def __init__(self) -> None:
        """Report the public limit contract."""
        super().__init__(f"limit must be between 1 and {MAX_LIMIT}")


class RoomNotFoundError(Exception):
    """No current cached room has the requested opaque ID."""


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


def records(blob: bytes, profile: Profile, tag: int) -> tuple[Record, ...]:
    """Ignore only the observed version sentinel; reject unexpected records."""
    result: list[Record] = []
    for key, payload in decode_frames(blob, profile.key).items():
        value = decode_value(payload)
        if key == "version" and type(value) is int:
            continue
        if not isinstance(key, str) or not isinstance(value, Record) or value.tag != tag:
            raise ValueFormatError(ValueIssue.RECORD)
        if tag == OBJECT_TAG and value.fields.get(1) != key:
            raise ValueFormatError(ValueIssue.IDENTITY)
        result.append(value)
    return tuple(result)


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
        """Fence two stores against observed changes and decode them fail-closed."""
        profile = self.profile if self.profile is not None else discover()
        paths = (profile.directory / "folder.hive", profile.directory / "object.hive")
        blobs = tuple(read_stable(path) for path in paths)
        if any(read_stable(path) != blob for path, blob in zip(paths, blobs, strict=True)):
            raise StorageError(StorageIssue.CHANGED)
        folder_records = records(blobs[0], profile, 37)
        object_records = records(blobs[1], profile, 38)
        by_url: dict[str, Room] = {}
        for folder in folder_records:
            raw = mapping(folder.fields.get(0))
            attrs = mapping(folder.fields.get(3))
            url = text(raw.get("resourceURL"))
            if url in by_url:
                raise ValueFormatError(ValueIssue.ROOM)
            by_url[url] = Room(
                id=hashlib.sha256(url.encode("utf-8")).hexdigest(),
                title=optional_text(attrs.get("Name")),
            )
        messages: list[Message] = []
        for record in object_records:
            raw = mapping(record.fields.get(0))
            attrs = mapping(record.fields.get(6))
            room = by_url.get(text(raw.get("parentFolder")))
            if room is None:
                raise StorageError(StorageIssue.ORPHAN)
            try:
                stamp = datetime.fromisoformat(text(attrs.get("Date")))
            except ValueError as exc:
                raise ValueFormatError(ValueIssue.DATE) from exc
            if stamp.tzinfo is None:
                raise ValueFormatError(ValueIssue.DATE)
            messages.append(
                Message(
                    id=text(record.fields.get(1)),
                    room_id=room.id,
                    text=optional_text(attrs.get("TextContent")),
                    sent_at=stamp,
                    direction_raw=optional_text(attrs.get("Direction")),
                )
            )
        return Snapshot(
            rooms=tuple(sorted(by_url.values(), key=lambda room: room.id)),
            messages=tuple(sorted(messages, key=lambda message: (message.sent_at, message.id))),
        )

    def rooms(self, limit: int = 100) -> Rooms:
        """Return a bounded room list in opaque-ID order."""
        if not 1 <= limit <= MAX_LIMIT:
            raise InvalidLimitError
        snapshot = self.snapshot()
        return Rooms(
            rooms=snapshot.rooms[:limit],
            total=len(snapshot.rooms),
            truncated=len(snapshot.rooms) > limit,
        )

    def messages(self, room_id: str, limit: int = 100) -> Messages:
        """Return the newest cached time window; timestamps are not causal order."""
        if not 1 <= limit <= MAX_LIMIT:
            raise InvalidLimitError
        snapshot = self.snapshot()
        if not any(room.id == room_id for room in snapshot.rooms):
            raise RoomNotFoundError
        selected = tuple(message for message in snapshot.messages if message.room_id == room_id)
        return Messages(
            messages=selected[-limit:], total=len(selected), truncated=len(selected) > limit
        )
