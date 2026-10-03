# Copyright (c) 2026 chatplus-core contributors
"""F3 design: a pure send state machine (no real sending).

Models the lifecycle Draft -> Submitting -> Submitted -> {Confirmed, Unknown}
from the follow-up plan. This module performs NO message sending, window
automation, network, or IO; it only computes the next immutable state. Real
delivery is never claimed (Submitted != delivered), and an Unknown outcome is
never retried automatically (uncertain != retryable): leaving Unknown requires
an explicit, user-initiated new draft.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import assert_never


class State(StrEnum):
    """Lifecycle states of one send attempt."""

    DRAFT = "draft"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


class Event(StrEnum):
    """The transitions a caller may request."""

    BEGIN = "begin"
    FAIL = "fail"
    SUBMIT = "submit"
    CONFIRM = "confirm"
    LOSE = "lose"


class SendTransitionError(Exception):
    """A requested event is not allowed from the current state."""

    def __init__(self, state: State, event: Event) -> None:
        """Carry the offending state and event as typed fields."""
        self.state: State = state
        self.event: Event = event
        super().__init__(f"event {event} is not allowed from state {state}")


@dataclass(frozen=True, slots=True)
class Send:
    """One immutable send attempt; a transition never loses the draft."""

    room_id: str
    draft: str
    state: State = State.DRAFT


def new_draft(room_id: str, body: str) -> Send:
    """Begin a fresh draft; the only way to act after an Unknown outcome."""
    return Send(room_id=room_id, draft=body)


def _require(send: Send, expected: State, event: Event) -> None:
    """Reject an event issued from the wrong state."""
    if send.state is not expected:
        raise SendTransitionError(send.state, event)


def advance(send: Send, event: Event) -> Send:
    """Compute the next state; forbidden transitions raise, the draft is kept."""
    match event:
        case Event.BEGIN:
            _require(send, State.DRAFT, event)
            return replace(send, state=State.SUBMITTING)
        case Event.FAIL:
            _require(send, State.SUBMITTING, event)
            return replace(send, state=State.DRAFT)
        case Event.SUBMIT:
            _require(send, State.SUBMITTING, event)
            return replace(send, state=State.SUBMITTED)
        case Event.CONFIRM:
            _require(send, State.SUBMITTED, event)
            return replace(send, state=State.CONFIRMED)
        case Event.LOSE:
            if send.state not in (State.SUBMITTING, State.SUBMITTED):
                raise SendTransitionError(send.state, event)
            return replace(send, state=State.UNKNOWN)
        case _:
            assert_never(event)


def recover(send: Send) -> Send:
    """After a crash, resolve an in-flight send to Unknown; never auto-resend."""
    if send.state in (State.SUBMITTING, State.SUBMITTED):
        return replace(send, state=State.UNKNOWN)
    return send


def is_terminal(send: Send) -> bool:
    """Report whether the send has reached Confirmed or Unknown (no more events)."""
    return send.state in (State.CONFIRMED, State.UNKNOWN)
