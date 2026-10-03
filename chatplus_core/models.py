# Copyright (c) 2026 chatplus-core contributors
"""tsuba-contract answers; no raw storage or credential fields.

A key that does not apply is absent, never null: answers are serialized
without `None` values, so optional fields stay off the wire.
"""

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Model(BaseModel):
    """Immutable wire value with camel-case field names."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class Health(Model):
    """Liveness, answered without a token."""

    ok: Literal[True] = True


class ContractInfo(Model):
    """The contract version this adapter answers."""

    version: str


class AdapterInfo(Model):
    """The adapter's own name and release."""

    name: str
    version: str


class ProviderInfo(Model):
    """The messenger behind the adapter."""

    id: str
    name: str


class AccountInfo(Model):
    """Whose view of the messenger the adapter reads."""

    kind: Literal["user", "bot", "official"]


class Capabilities(Model):
    """What the adapter performs end to end; an absent feature is unsupported."""

    ok: Literal[True] = True
    contract: ContractInfo
    adapter: AdapterInfo
    provider: ProviderInfo
    account: AccountInfo
    revision: str
    features: dict[str, dict[str, str | int]]


class LastMessage(Model):
    """The newest message of a room, for a list preview."""

    text: str
    from_me: bool


class Room(Model):
    """A conversation as the account's own client lists it."""

    id: str
    title: str
    kind: Literal["direct", "group", "channel"]
    unread: int = 0
    last_activity_at: datetime | None = None
    last_message: LastMessage | None = None


class Message(Model):
    """One cached message; `text` is empty when it carries none."""

    id: str
    room_id: str
    sender: str
    sender_id: str | None = None
    text: str
    kind: Literal["text", "media", "service"]
    sent_at: datetime
    from_me: bool


class RoomsAnswer(Model):
    """The rooms, and whether the adapter stopped at its own cap."""

    ok: Literal[True] = True
    rooms: tuple[Room, ...]
    complete: bool


class MessagesAnswer(Model):
    """A window of one room's messages, oldest first."""

    ok: Literal[True] = True
    messages: tuple[Message, ...]
    has_earlier: bool


class StreamReady(Model):
    """The first stream event: whether it resumed, and each room's position."""

    ok: Literal[True] = True
    resumed: bool
    cursor: dict[str, str]


class StreamMessage(Model):
    """A message the stream delivers."""

    ok: Literal[True] = True
    message: Message


class StreamCursor(Model):
    """The last message id per room, sent after the messages it covers."""

    ok: Literal[True] = True
    cursor: dict[str, str]


class StreamPing(Model):
    """A keep-alive while the stream is quiet."""

    ok: Literal[True] = True


class SendRequest(Model):
    """Text to send into an existing room."""

    room_id: str
    text: str
    reply_to: str | None = None


class SendAnswer(Model):
    """The message the client stored for a submitted send."""

    ok: Literal[True] = True
    message: Message
