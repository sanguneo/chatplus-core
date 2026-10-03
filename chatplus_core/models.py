# Copyright (c) 2026 chatplus-core contributors
"""Public read-only response models; no raw storage or credential fields."""

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Model(BaseModel):
    """Immutable API value with camel-case wire fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class Room(Model):
    """An opaque local room identity and the cached room name."""

    id: str
    title: str | None


class Message(Model):
    """Cached text, without claiming full rich-message or delivery coverage."""

    id: str
    room_id: str
    text: str | None
    sent_at: datetime
    direction_raw: str | None
    content_type: str | None = None
    has_attachment: bool = False


class Rooms(Model):
    """A bounded list from this local cache read."""

    ok: Literal[True] = True
    rooms: tuple[Room, ...]
    total: int
    truncated: bool
    coverage: Literal["local-cache"] = "local-cache"


class Messages(Model):
    """Newest cached records, returned in ascending timestamp and ID order."""

    ok: Literal[True] = True
    messages: tuple[Message, ...]
    total: int
    truncated: bool
    next_cursor: str | None = None
    coverage: Literal["local-cache"] = "local-cache"
    order: Literal["timestamp-id"] = "timestamp-id"
