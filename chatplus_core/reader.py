# Copyright (c) 2026 chatplus-core contributors
"""Map verified local fields to the tsuba room and message shapes."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal
from urllib.parse import urlsplit

from chatplus_core.hive import HiveFormatError, decode_frames
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
MOBILE_DIGITS: Final = 12
MOBILE_PREFIX: Final = "8210"
COUNTRY_CODE: Final = "82"
TEL: Final = "tel:"
# The client's contacts: records under numeric ids, a number list in field 1 and
# the shown name in field 2 (observed in client 1.5.4.6).
CONTACT_STORE: Final = "contact_list.hive"
CONTACT_TAG: Final = 133
CONTACT_NUMBERS: Final = 1
CONTACT_NAME: Final = 2
SEOUL_PREFIX: Final = "02"
SEOUL_LENGTHS: Final = (9, 10)
AREA_LENGTHS: Final = (10, 11)
SERVICE_LENGTH: Final = 8
NAME_FAILURES: Final = (OSError, StorageError, HiveFormatError, ValueFormatError, UnicodeError)
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


def national_mobile(uri: str) -> str | None:
    """Return a Korean mobile tel: URI as its national 010 number, else None.

    Only +82 10 XXXXXXXX (12 digits) converts, unambiguously, to 010XXXXXXXX. A
    landline, a short code or a non-tel URI returns None, so a send refuses rather
    than guess a number the client might resolve to someone else.
    """
    if not uri.startswith("tel:"):
        return None
    digits = "".join(ch for ch in uri if ch.isdigit())
    if len(digits) == MOBILE_DIGITS and digits.startswith(MOBILE_PREFIX):
        return "0" + digits[2:]
    return None


def national_number(uri: str) -> str | None:
    """Return a tel: URI as Korean national digits (+82 becomes 0), or None."""
    if not uri.startswith(TEL):
        return None
    number = uri[len(TEL) :].split(";", 1)[0].strip()
    digits = "".join(ch for ch in number if ch.isdigit())
    if not digits:
        return None
    if not number.startswith("+"):
        return digits
    if digits.startswith(COUNTRY_CODE) and len(digits) > len(COUNTRY_CODE):
        return "0" + digits[len(COUNTRY_CODE) :]
    return None


def format_number(digits: str) -> str:
    """Group national digits as a Korean phone shows them; anything else as given."""
    if digits.startswith(SEOUL_PREFIX) and len(digits) in SEOUL_LENGTHS:
        return f"{digits[:2]}-{digits[2:-4]}-{digits[-4:]}"
    if digits.startswith("0") and len(digits) in AREA_LENGTHS:
        return f"{digits[:3]}-{digits[3:-4]}-{digits[-4:]}"
    if len(digits) == SERVICE_LENGTH and digits.startswith("1"):
        return f"{digits[:4]}-{digits[4:]}"
    return digits


def address_label(address: str, names: dict[str, str]) -> str:
    """Name a tel: address by the client's contact, else show its number without the scheme."""
    if not address.startswith(TEL):
        return address
    national = national_number(address)
    if national is None:
        return address[len(TEL) :].split(";", 1)[0].strip()
    return names.get(national) or format_number(national)


def contact_names(blob: bytes, profile: Profile) -> dict[str, str]:
    """Map each national number to the one name the client's contacts give it.

    The box's own bookkeeping (version, sync times) is plain values under named
    keys and is skipped. A number that two contacts give different names maps to
    no name, so the number is shown rather than one of the names guessed.
    """
    found: dict[str, set[str]] = {}
    for payload in decode_frames(blob, profile.key).values():
        value = decode_value(payload)
        if not isinstance(value, Record):
            continue
        if value.tag != CONTACT_TAG:
            raise ValueFormatError(ValueIssue.RECORD)
        name = (optional_text(value.fields.get(CONTACT_NAME)) or "").strip()
        numbers = value.fields.get(CONTACT_NUMBERS)
        if not name or not isinstance(numbers, list):
            continue
        for number in numbers:
            national = national_number(TEL + number) if isinstance(number, str) else None
            if national is not None:
                found.setdefault(national, set()).add(name)
    return {number: next(iter(names)) for number, names in found.items() if len(names) == 1}


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


def to_message(record: Record, room_id: str, names: dict[str, str]) -> Message:
    """Map one cached message; a non-text content type is media, without bytes."""
    attrs = mapping(record.fields.get(6))
    content_type = optional_text(attrs.get("Content-Type"))
    textual = content_type is None or content_type.lower().startswith("text/")
    address = optional_text(attrs.get("From")) or ""
    return Message(
        id=text(record.fields.get(1)),
        room_id=room_id,
        sender=address_label(address, names),
        sender_id=address or None,
        text=optional_text(attrs.get("TextContent")) or "",
        kind="text" if textual else "media",
        sent_at=timestamp(attrs.get("Date")),
        from_me=optional_text(attrs.get("Direction")) == OUTGOING,
    )


def room_title(attrs: dict[str | int, Value], names: dict[str, str]) -> str:
    """Title a conversation by its name, else its recipients, each tel: address named."""
    name = optional_text(attrs.get("Name"))
    if name:
        return address_label(name, names)
    recipients = attrs.get("Recipients")
    if not isinstance(recipients, list):
        return ""
    return ", ".join(
        address_label(recipient, names) for recipient in recipients if isinstance(recipient, str)
    )


def to_room(
    room_id: str, attrs: dict[str | int, Value], newest: Message | None, names: dict[str, str]
) -> Room:
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
        title=room_title(attrs, names),
        kind=room_kind(attrs.get("Recipients")),
        last_activity_at=activity,
        last_message=preview,
    )


def folder_of(
    parent: str, folders: dict[str, Folder], by_path: dict[str, list[Folder]]
) -> Folder | None:
    """Place a message by its folder's exact URL, else by the one folder at the same path.

    A message sent from this PC can name its folder through another host of the
    same message store; the path (store and folder id) still names one folder. No
    match, or more than one, returns None: the message is skipped as pending (a
    conversation still being created) rather than guessed at or taken as fatal.
    """
    exact = folders.get(parent)
    if exact is not None:
        return exact
    candidates = by_path.get(urlsplit(parent).path, [])
    return candidates[0] if len(candidates) == 1 else None


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
        names = self.names(profile)
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
            folder = folder_of(parent, folders, by_path)
            if folder is None:
                continue
            messages.append(to_message(record, folder[0], names))
        messages.sort(key=lambda message: (message.sent_at, message.id))
        newest = {message.room_id: message for message in messages}
        rooms = sorted(
            (
                to_room(room_id, attrs, newest.get(room_id), names)
                for room_id, attrs in folders.values()
            ),
            key=lambda room: room.id,
        )
        rooms.sort(key=lambda room: room.last_activity_at or EARLIEST, reverse=True)
        return Snapshot(rooms=tuple(rooms), messages=tuple(messages))

    def names(self, profile: Profile) -> dict[str, str]:
        """Read the client's contact names; without a readable contact box, numbers stand in.

        Names only label numbers. A contact box that is missing, mid-write or in an
        unknown shape leaves every number unnamed instead of failing the rooms and
        messages, which do not depend on it.
        """
        try:
            return contact_names(read_stable(profile.directory / CONTACT_STORE), profile)
        except NAME_FAILURES:
            return {}

    def recipient_number(self, room_id: str) -> str | None:
        """Return the room's lone mobile recipient in national form, or None if not derivable."""
        return self.recipient_numbers().get(room_id)

    def recipient_numbers(self) -> dict[str, str | None]:
        """Return every room's lone mobile recipient in national form, None where not derivable."""
        profile = self.profile if self.profile is not None else discover()
        epoch = profile.directory.name
        numbers: dict[str, str | None] = {}
        for folder in records(read_stable(profile.directory / "folder.hive"), profile, 37):
            raw = folder.fields.get(0)
            if not isinstance(raw, dict) or "resourceURL" not in raw:
                continue
            attrs = folder.fields.get(3)
            recipients = attrs.get("Recipients") if isinstance(attrs, dict) else None
            only = recipients[0] if isinstance(recipients, list) and len(recipients) == 1 else None
            room_id = room_identity(epoch, text(raw.get("resourceURL")))
            numbers[room_id] = national_mobile(only) if isinstance(only, str) else None
        return numbers

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
