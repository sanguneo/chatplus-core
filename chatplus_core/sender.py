# Copyright (c) 2026 chatplus-core contributors
"""The write line and the send read-back for POST /send, apart from Windows.

Sending drives the running client's own window; the window driver is a seam
(`Sender`) the Windows-only backend fills. What stays here is independent of the
desktop: the typed send failures and their codes, the shared write line (one
write at a time, at most four admitted), and the read-back that turns a submitted
send into the message the client stored. `submitted` is not delivered, and a send
the read-back cannot find is reported uncertain rather than retried.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Final, Protocol

import anyio

from chatplus_core.envelope import ErrorCode
from chatplus_core.models import Message, Room
from chatplus_core.reader import Reader

MAX_ADMITTED: Final = 4
CONFIRM_POLL_SECONDS: Final = 0.5


class SendError(Exception):
    """A send that did not happen, carrying the code the client should see."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        """Keep the tsuba code and a person-readable reason as typed fields."""
        self.code: ErrorCode = code
        self.message: str = message
        super().__init__(message)


class WriteLineFullError(SendError):
    """The adapter already holds as many writes as it will admit; nothing was sent."""

    def __init__(self) -> None:
        """Refuse with the rate-limited code."""
        super().__init__(ErrorCode.RATE_LIMITED, "The adapter's write line is full")


class ClientUnavailableError(SendError):
    """The client is not there to take a message; nothing was sent."""

    def __init__(self) -> None:
        """Refuse with the reauth-required code: a person acts where the adapter runs."""
        super().__init__(ErrorCode.REAUTH_REQUIRED, "The client is not running")


class RoomUnverifiedError(SendError):
    """The adapter could not open and confirm the target room; nothing was sent."""

    def __init__(self) -> None:
        """Refuse with the forbidden code rather than send to an unconfirmed room."""
        super().__init__(
            ErrorCode.FORBIDDEN, "The adapter could not confirm the room; nothing sent"
        )


class Sender(Protocol):
    """Drive the running client to place one message in one room's conversation."""

    async def submit(self, room: Room, text: str) -> None:
        """Place the text in the room and submit it, or raise a SendError having sent nothing."""
        ...


class WriteLine:
    """One write at a time, at most a cap admitted; the next refused before any client touch."""

    def __init__(self, capacity: int = MAX_ADMITTED) -> None:
        """Start an empty line with its admission cap."""
        self._capacity: int = capacity
        self._admitted: int = 0
        self._lock: anyio.Lock = anyio.Lock()

    @asynccontextmanager
    async def admit(self) -> AsyncGenerator[None]:
        """Hold a slot while one write runs; refuse past the cap having waited for nothing."""
        if self._admitted >= self._capacity:
            raise WriteLineFullError
        self._admitted += 1
        try:
            async with self._lock:
                yield
        finally:
            self._admitted -= 1


async def confirm(
    reader: Reader,
    room_id: str,
    text: str,
    before: frozenset[str],
    *,
    deadline_seconds: float,
) -> Message | None:
    """Return the message the client stored for a submitted send, or None by the deadline.

    A match is a newly cached outgoing message of this room with this text that was
    not present before the send, newest first. No match by the deadline means the
    outcome is unknown: the caller says so and does not retry.
    """
    with anyio.move_on_after(deadline_seconds):
        while True:
            snapshot = await anyio.to_thread.run_sync(reader.snapshot)
            found = next(
                (
                    message
                    for message in reversed(snapshot.messages)
                    if message.room_id == room_id
                    and message.from_me
                    and message.text == text
                    and message.id not in before
                ),
                None,
            )
            if found is not None:
                return found
            await anyio.sleep(CONFIRM_POLL_SECONDS)
    return None
