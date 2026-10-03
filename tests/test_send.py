# Copyright (c) 2026 chatplus-core contributors
"""Synthetic tests for the F3 send state machine (no real sending)."""

from dataclasses import fields

import pytest

from chatplus_core import send as send_module
from chatplus_core.send import (
    Event,
    Send,
    SendTransitionError,
    State,
    advance,
    is_terminal,
    new_draft,
    recover,
)


def test_happy_path_reaches_confirmed_preserving_target() -> None:
    s = new_draft("room-abc", "hello")
    s = advance(s, Event.BEGIN)
    s = advance(s, Event.SUBMIT)
    s = advance(s, Event.CONFIRM)
    assert s.state is State.CONFIRMED
    assert s.room_id == "room-abc"
    assert s.draft == "hello"


def test_submitting_failure_preserves_draft_and_returns_to_draft() -> None:
    s = advance(new_draft("r", "keep me"), Event.BEGIN)
    s = advance(s, Event.FAIL)
    assert s.state is State.DRAFT
    assert s.draft == "keep me"


@pytest.mark.parametrize("midpoint", [None, Event.SUBMIT])
def test_lose_yields_unknown_from_in_flight(midpoint: Event | None) -> None:
    s = advance(new_draft("r", "d"), Event.BEGIN)
    if midpoint is not None:
        s = advance(s, midpoint)
    s = advance(s, Event.LOSE)
    assert s.state is State.UNKNOWN
    assert s.draft == "d"
    assert is_terminal(s)


@pytest.mark.parametrize(
    ("state", "event"),
    [
        (State.DRAFT, Event.SUBMIT),
        (State.DRAFT, Event.CONFIRM),
        (State.DRAFT, Event.LOSE),
        (State.SUBMITTED, Event.BEGIN),
    ],
)
def test_forbidden_transitions_raise(state: State, event: Event) -> None:
    with pytest.raises(SendTransitionError):
        _ = advance(Send(room_id="r", draft="d", state=state), event)


@pytest.mark.parametrize("terminal", [State.UNKNOWN, State.CONFIRMED])
@pytest.mark.parametrize("event", list(Event))
def test_terminal_states_accept_no_event(terminal: State, event: Event) -> None:
    with pytest.raises(SendTransitionError):
        _ = advance(Send(room_id="r", draft="d", state=terminal), event)


def test_unknown_is_not_retryable_only_a_new_draft_continues() -> None:
    lost = Send(room_id="r", draft="d", state=State.UNKNOWN)
    assert not hasattr(send_module, "retry")
    assert not hasattr(send_module, "resend")
    fresh = new_draft(lost.room_id, lost.draft)
    assert fresh.state is State.DRAFT
    assert fresh is not lost


def test_crash_recovery_resolves_in_flight_to_unknown() -> None:
    assert recover(Send("r", "d", State.SUBMITTING)).state is State.UNKNOWN
    assert recover(Send("r", "d", State.SUBMITTED)).state is State.UNKNOWN
    assert recover(Send("r", "d", State.DRAFT)).state is State.DRAFT
    assert recover(Send("r", "d", State.CONFIRMED)).state is State.CONFIRMED


def test_no_delivery_or_receipt_claim_on_the_state() -> None:
    names = {field.name for field in fields(Send)}
    assert "delivered" not in names
    assert "receipt" not in names
