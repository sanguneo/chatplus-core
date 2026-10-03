# Copyright (c) 2026 chatplus-core contributors
"""Pure core for the F2 message stream: per-room positions, resume, framing.

The SSE endpoint is a thin real-time loop around these pure functions. A room
first seen after the stream starts begins "from now" (its backlog is not
replayed) - a documented gap rather than a silent full replay. A resume hint
whose message id is no longer present also falls back to "from now".
"""

from collections.abc import Iterable
from datetime import UTC, datetime

from chatplus_core.models import Message
from chatplus_core.reader import Snapshot

Key = tuple[datetime, str]
Positions = dict[str, Key]
MIN_KEY: Key = (datetime.min.replace(tzinfo=UTC), "")


def _key(message: Message) -> Key:
    """Order a message by (sent_at, id), matching the reader's ordering."""
    return (message.sent_at, message.id)


def parse_since(values: Iterable[str]) -> dict[str, str]:
    """Parse repeated `<roomId>:<messageId>` resume hints (roomId is colon-free hex)."""
    result: dict[str, str] = {}
    for value in values:
        room, separator, message_id = value.partition(":")
        if separator and room and message_id:
            result[room] = message_id
    return result


def initial_positions(snapshot: Snapshot, since: dict[str, str]) -> Positions:
    """Seed each room's position: a resumed message id if still present, else from now."""
    positions: Positions = {}
    for room in snapshot.rooms:
        room_messages = [m for m in snapshot.messages if m.room_id == room.id]
        resumed: Key | None = None
        wanted = since.get(room.id)
        if wanted is not None:
            match = next((m for m in room_messages if m.id == wanted), None)
            if match is not None:
                resumed = _key(match)
        positions[room.id] = resumed or (_key(room_messages[-1]) if room_messages else MIN_KEY)
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


def sse_frame(event: str, data: str) -> str:
    """Format one Server-Sent Events frame."""
    return f"event: {event}\ndata: {data}\n\n"
