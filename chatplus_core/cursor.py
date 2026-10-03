# Copyright (c) 2026 chatplus-core contributors
"""Opaque, room-bound pagination cursors over one room's message history.

A cursor names a position (sent_at, message_id) within a specific room. It is
bound to its room id so it cannot be replayed against another room or, after an
account epoch change, against a room id that no longer exists (that surfaces as
an explicit error rather than a silent restart).
"""

import base64
import binascii
from datetime import datetime

from pydantic import ValidationError

from chatplus_core.models import Model


class CursorError(ValueError):
    """A pagination cursor is malformed or does not belong to the requested room."""


class CursorPayload(Model):
    """The decoded position a cursor points at within one room's history."""

    room_id: str
    sent_at: datetime
    message_id: str


def encode_cursor(room_id: str, sent_at: datetime, message_id: str) -> str:
    """Serialize a room-bound position into an opaque URL-safe token."""
    payload = CursorPayload(room_id=room_id, sent_at=sent_at, message_id=message_id)
    return base64.urlsafe_b64encode(payload.model_dump_json(by_alias=True).encode()).decode()


def decode_cursor(token: str, room_id: str) -> tuple[datetime, str]:
    """Parse a cursor and confirm it belongs to the requested room."""
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        payload = CursorPayload.model_validate_json(raw)
    except (binascii.Error, ValidationError) as exc:
        raise CursorError from exc
    if payload.room_id != room_id:
        raise CursorError
    return (payload.sent_at, payload.message_id)
