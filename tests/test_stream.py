# Copyright (c) 2026 chatplus-core contributors
"""Synthetic tests for the pure stream core (no sleeps, no IO)."""

from datetime import UTC, datetime

import pytest

from chatplus_core.models import Message, Room
from chatplus_core.reader import Snapshot
from chatplus_core.stream import (
    MIN_KEY,
    ResumeError,
    advance,
    cursor_map,
    initial_positions,
    parse_since,
    position_cursor,
    sse_frame,
)


def _msg(room: str, mid: str, minute: int) -> Message:
    return Message(
        id=mid,
        room_id=room,
        sender="s",
        text="t",
        kind="text",
        sent_at=datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
        from_me=False,
    )


def _snapshot(rooms: tuple[str, ...], messages: tuple[Message, ...]) -> Snapshot:
    return Snapshot(
        rooms=tuple(Room(id=room, title="", kind="direct") for room in rooms),
        messages=messages,
    )


def test_parse_since_ends_the_room_id_at_the_first_colon() -> None:
    assert parse_since(["a:1", "d:3:4"]) == {"a": "1", "d": "3:4"}


@pytest.mark.parametrize("hint", ["bad", ":2", "c:"])
def test_parse_since_refuses_malformed_cursors(hint: str) -> None:
    with pytest.raises(ResumeError):
        _ = parse_since([hint])


def test_parse_since_refuses_a_room_named_twice() -> None:
    with pytest.raises(ResumeError):
        _ = parse_since(["a:1", "a:2"])


def test_initial_positions_resume_named_rooms_and_start_others_from_now() -> None:
    m1 = _msg("r", "m1", 1)
    m2 = _msg("r", "m2", 2)
    snap = _snapshot(("r", "empty"), (m1, m2))
    assert initial_positions(snap, {"r": "m1"})["r"] == (m1.sent_at, "m1")
    assert initial_positions(snap, {"r": "m1"})["empty"] == MIN_KEY
    assert initial_positions(snap, {})["r"] == (m2.sent_at, "m2")


@pytest.mark.parametrize("since", [{"r": "gone"}, {"nowhere": "m1"}, {"empty": "m1"}])
def test_initial_positions_refuse_a_cursor_they_cannot_honour(since: dict[str, str]) -> None:
    snap = _snapshot(("r", "empty"), (_msg("r", "m1", 1),))
    with pytest.raises(ResumeError):
        _ = initial_positions(snap, since)


def test_advance_emits_only_newer_and_is_idempotent() -> None:
    m1 = _msg("r", "m1", 1)
    m2 = _msg("r", "m2", 2)
    m3 = _msg("r", "m3", 3)
    positions = initial_positions(_snapshot(("r",), (m1, m2)), {})
    emitted, positions = advance(_snapshot(("r",), (m1, m2, m3)), positions)
    assert [m.id for m in emitted] == ["m3"]
    assert positions["r"] == (m3.sent_at, "m3")
    again, positions = advance(_snapshot(("r",), (m1, m2, m3)), positions)
    assert again == ()


def test_advance_new_room_starts_from_now_without_backlog() -> None:
    m1 = _msg("r", "m1", 1)
    positions = initial_positions(_snapshot(("r",), (m1,)), {})
    b1 = _msg("r2", "b1", 1)
    b2 = _msg("r2", "b2", 2)
    emitted, positions = advance(_snapshot(("r", "r2"), (m1, b1, b2)), positions)
    assert emitted == ()
    assert positions["r2"] == (b2.sent_at, "b2")
    b3 = _msg("r2", "b3", 3)
    later, positions = advance(_snapshot(("r", "r2"), (m1, b1, b2, b3)), positions)
    assert [m.id for m in later] == ["b3"]


def test_cursor_map_is_last_id_per_room() -> None:
    a1 = _msg("a", "a1", 1)
    a2 = _msg("a", "a2", 2)
    b1 = _msg("b", "b1", 1)
    assert cursor_map((a1, a2, b1)) == {"a": "a2", "b": "b1"}


def test_position_cursor_omits_rooms_without_messages() -> None:
    m2 = _msg("r", "m2", 2)
    assert position_cursor({"r": (m2.sent_at, "m2"), "empty": MIN_KEY}) == {"r": "m2"}


def test_sse_frame_format() -> None:
    assert sse_frame("ready", "{}") == "event: ready\ndata: {}\n\n"
