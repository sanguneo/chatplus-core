# Copyright (c) 2026 chatplus-core contributors
"""Pure core for the message stream: per-room positions, resume and framing.

The SSE endpoint is a thin real-time loop around these pure functions. A room
the client does not name starts from now, and so does a room first seen after
the stream starts (its backlog is not replayed). A named room resumes after its
message id; a cursor that cannot be honoured is refused, never downgraded to now.
"""

from collections.abc import Iterable
from datetime import UTC, datetime

from chatplus_core.models import Message
from chatplus_core.reader import Snapshot

Key = tuple[datetime, str]
Positions = dict[str, Key]
MIN_KEY: Key = (datetime.min.replace(tzinfo=UTC), "")


class ResumeError(ValueError):
    """A resume cursor is malformed, names a room twice, or names nothing cached."""


def _key(message: Message) -> Key:
    """Order a message by (sent_at, id), matching the reader's ordering."""
    return (message.sent_at, message.id)


def parse_since(values: Iterable[str]) -> dict[str, str]:
    """Parse repeated `<roomId>:<messageId>` cursors, refusing malformed or repeated rooms.

    Room ids are colon-free hex, so the first colon ends the room id even when a
    message id carries colons of its own.
    """
    result: dict[str, str] = {}
    for value in values:
        room, separator, message_id = value.partition(":")
        if not (separator and room and message_id) or room in result:
            raise ResumeError
        result[room] = message_id
    return result


def initial_positions(snapshot: Snapshot, since: dict[str, str]) -> Positions:
    """Seed each room's position: a named room at its message id, any other from now."""
    by_room: dict[str, list[Message]] = {room.id: [] for room in snapshot.rooms}
    for message in snapshot.messages:
        by_room.setdefault(message.room_id, []).append(message)
    positions: Positions = {}
    for room_id, wanted in since.items():
        match = next((m for m in by_room.get(room_id, []) if m.id == wanted), None)
        if match is None:
            raise ResumeError
        positions[room_id] = _key(match)
    for room_id, messages in by_room.items():
        if room_id not in positions:
            positions[room_id] = _key(messages[-1]) if messages else MIN_KEY
    return positions


def advance(snapshot: Snapshot, positions: Positions) -> tuple[tuple[Message, ...], Positions]:
    """Return messages newer than each room's position and the updated positions.

    A room absent from `positions` is newly seen: its backlog is not emitted and
    its position is set to the room's current maximum (from now).
    """
    emitted: list[Message] = []
    updated = dict(positions)
    for message in snapshot.messages:
        position = positions.get(message.room_id)
        if position is None:
            continue
        key = _key(message)
        if key > position:
            emitted.append(message)
            updated[message.room_id] = key
    for room in snapshot.rooms:
        if room.id not in updated:
            room_keys = [_key(m) for m in snapshot.messages if m.room_id == room.id]
            updated[room.id] = max(room_keys) if room_keys else MIN_KEY
    return tuple(emitted), updated


def cursor_map(emitted: Iterable[Message]) -> dict[str, str]:
    """Return the last emitted message id per room, for the cursor event."""
    cursors: dict[str, str] = {}
    for message in emitted:
        cursors[message.room_id] = message.id
    return cursors


def position_cursor(positions: Positions) -> dict[str, str]:
    """Return each room's current message id; a room with no messages has none."""
    return {room_id: key[1] for room_id, key in positions.items() if key[1]}


def sse_frame(event: str, data: str) -> str:
    """Format one Server-Sent Events frame."""
    return f"event: {event}\ndata: {data}\n\n"
