# Copyright (c) 2026 chatplus-core contributors
"""The tsuba adapter over HTTP and SSE; only the reads it performs are declared."""

import binascii
import hashlib
import hmac
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated, Final

import anyio
from cryptography.exceptions import InvalidTag
from fastapi import Depends, FastAPI, HTTPException, Query, Request, params
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from chatplus_core.envelope import ErrorCode, error_body, failure
from chatplus_core.hive import HiveFormatError
from chatplus_core.models import (
    AccountInfo,
    AdapterInfo,
    Capabilities,
    ContractInfo,
    Health,
    MessagesAnswer,
    Model,
    ProviderInfo,
    Room,
    RoomsAnswer,
    SendAnswer,
    SendRequest,
    StreamCursor,
    StreamMessage,
    StreamPing,
    StreamReady,
)
from chatplus_core.reader import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    InvalidLimitError,
    MessageNotFoundError,
    Reader,
    RoomNotFoundError,
    Snapshot,
)
from chatplus_core.sender import Sender, SendError, WriteLine, confirm
from chatplus_core.storage import StorageError
from chatplus_core.stream import (
    Positions,
    ResumeError,
    advance,
    cursor_map,
    initial_positions,
    parse_since,
    position_cursor,
    sse_frame,
)
from chatplus_core.values import ValueFormatError

CONTRACT_VERSION: Final = "0.2.5"
ADAPTER_VERSION: Final = "0.3.0"
MIN_TOKEN_BYTES: Final = 32
MAX_ID_LENGTH: Final = 512
MAX_STREAMS: Final = 8
STREAM_POLL_SECONDS: Final = 1.0
STREAM_PING_SECONDS: Final = 15.0
# The RCS Universal Profile single-message text limit, in code points
# (GSMA UP; gatewayapi.com/help-center/rcs-character-limits, twilio RCS char count).
MAX_SEND_LENGTH: Final = 3072
SEND_CONFIRM_SECONDS: Final = 10.0
FEATURES: Final[dict[str, dict[str, str | int]]] = {
    "rooms.list": {},
    "messages.read": {"paging": "before-id", "maxLimit": MAX_LIMIT},
    "messages.stream": {"resume": "cursor"},
}
SEND_FEATURE: Final[dict[str, str | int]] = {"maxLength": MAX_SEND_LENGTH, "receipt": "stored"}
UNSUPPORTED: Final = (
    ("GET", "/media"),
    ("POST", "/edit"),
    ("POST", "/delete"),
    ("POST", "/react"),
    ("POST", "/read"),
)
READ_FAILURES: Final = (
    OSError,
    StorageError,
    InvalidTag,
    ValueFormatError,
    HiveFormatError,
    UnicodeError,
    binascii.Error,
)
UNREADABLE: Final = "The local cache could not be read"
CHALLENGE: Final = {"WWW-Authenticate": "Bearer"}


class ConfigurationError(Exception):
    """The adapter cannot start with the supplied configuration."""

    def __init__(self) -> None:
        """Describe the startup requirement without echoing the token."""
        super().__init__(f"CHATPLUS_TOKEN must contain at least {MIN_TOKEN_BYTES} bytes")


def declaration(*, can_send: bool) -> Capabilities:
    """Declare what this adapter performs end to end, revised by a hash of the content."""
    features = {**FEATURES, **({"messages.send": SEND_FEATURE} if can_send else {})}
    draft = Capabilities(
        contract=ContractInfo(version=CONTRACT_VERSION),
        adapter=AdapterInfo(name="chatplus-core", version=ADAPTER_VERSION),
        provider=ProviderInfo(id="chatplus", name="채팅+"),
        account=AccountInfo(kind="user"),
        revision="",
        features=features,
    )
    content = draft.model_dump_json(by_alias=True, exclude={"ok", "revision"})
    revision = hashlib.sha256(content.encode()).hexdigest()[:16]
    return draft.model_copy(update={"revision": revision})


def event(name: str, payload: Model) -> str:
    """Frame one stream event, leaving keys that do not apply off the wire."""
    return sse_frame(name, payload.model_dump_json(by_alias=True, exclude_none=True))


async def stream_events(
    reader: Reader,
    snapshot: Snapshot,
    positions: Positions,
    *,
    resumed: bool,
    poll_seconds: float = STREAM_POLL_SECONDS,
) -> AsyncGenerator[str, None]:
    """Send ready, then each room's newer messages and a cursor, pinging while quiet."""
    yield event("ready", StreamReady(resumed=resumed, cursor=position_cursor(positions)))
    quiet = 0.0
    while True:
        emitted, positions = advance(snapshot, positions)
        for message in emitted:
            yield event("message", StreamMessage(message=message))
        if emitted:
            yield event("cursor", StreamCursor(cursor=cursor_map(emitted)))
            quiet = 0.0
        elif quiet >= STREAM_PING_SECONDS:
            yield event("ping", StreamPing())
            quiet = 0.0
        await anyio.sleep(poll_seconds)
        quiet += poll_seconds
        try:
            snapshot = await anyio.to_thread.run_sync(reader.snapshot)
        except READ_FAILURES:
            yield sse_frame("error", json.dumps(error_body(ErrorCode.INTERNAL, UNREADABLE)))
            return


@dataclass(slots=True)
class StreamSlots:
    """Live streams, so one past the limit is refused with BUSY."""

    limit: int
    live: int = 0


async def held(slots: StreamSlots, events: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
    """Hold a slot from a stream's first event until it ends, however it ends.

    Counting from the first event rather than from admission means a response
    whose body never starts cannot leak a slot.
    """
    slots.live += 1
    try:
        async for frame in events:
            yield frame
    finally:
        slots.live -= 1


async def open_stream(reader: Reader, slots: StreamSlots, since: list[str]) -> Response:
    """Admit a stream, refusing a cursor it cannot honour before the first event."""
    if slots.live >= slots.limit:
        return failure(ErrorCode.BUSY, "The adapter holds as many streams as it will")
    hints = parse_since(since)
    snapshot = await anyio.to_thread.run_sync(reader.snapshot)
    positions = initial_positions(snapshot, hints)
    return StreamingResponse(
        held(slots, stream_events(reader, snapshot, positions, resumed=bool(hints))),
        media_type="text/event-stream",
        headers={"cache-control": "no-store"},
    )


async def http_error(_request: Request, exc: Exception) -> JSONResponse:
    """Keep routing and authentication refusals in the shared envelope."""
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    headers = exc.headers
    match exc.status_code:
        case HTTPStatus.UNAUTHORIZED:
            return failure(ErrorCode.UNAUTHORIZED, "Bearer token required", headers)
        case HTTPStatus.NOT_FOUND:
            return failure(ErrorCode.NOT_FOUND, "No such route", headers)
        case HTTPStatus.METHOD_NOT_ALLOWED:
            return failure(
                ErrorCode.METHOD_NOT_ALLOWED, "The route answers another method", headers
            )
        case status if status < HTTPStatus.INTERNAL_SERVER_ERROR:
            return failure(ErrorCode.INVALID_REQUEST, "Request refused", headers)
        case _:
            return failure(ErrorCode.INTERNAL, "The adapter failed", headers)


def refusal(
    code: ErrorCode, message: str
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Build a handler answering one kind of refusal with its fixed code."""

    async def handle(_request: Request, _exc: Exception) -> JSONResponse:
        return failure(code, message)

    return handle


REFUSALS: Final[tuple[tuple[type[Exception], ErrorCode, str], ...]] = (
    (RequestValidationError, ErrorCode.INVALID_REQUEST, "Invalid request parameters"),
    (InvalidLimitError, ErrorCode.INVALID_REQUEST, "limit is outside the declared maxLimit"),
    (ResumeError, ErrorCode.INVALID_REQUEST, "The stream cannot resume from that cursor"),
    (RoomNotFoundError, ErrorCode.NOT_FOUND, "No such room in the local cache"),
    (MessageNotFoundError, ErrorCode.NOT_FOUND, "No such message in that room"),
    *((kind, ErrorCode.INTERNAL, UNREADABLE) for kind in READ_FAILURES),
    (Exception, ErrorCode.INTERNAL, "The adapter failed"),
)


def _install_error_handlers(app: FastAPI) -> None:
    """Route every refusal through the shared failure envelope."""
    app.add_exception_handler(StarletteHTTPException, http_error)
    for kind, code, message in REFUSALS:
        app.add_exception_handler(kind, refusal(code, message))


_BEARER = HTTPBearer(auto_error=False)


def _require_token(expected: bytes) -> Callable[[HTTPAuthorizationCredentials | None], None]:
    """Build the bearer-token check every route but /health depends on."""

    def authenticate(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    ) -> None:
        if credentials is None:
            raise HTTPException(status_code=401, headers=CHALLENGE)
        actual = hashlib.sha256(credentials.credentials.encode("utf-8")).digest()
        if not hmac.compare_digest(expected, actual):
            raise HTTPException(status_code=401, headers=CHALLENGE)

    return authenticate


async def not_supported() -> JSONResponse:
    """Answer a documented route whose feature this adapter does not declare."""
    return failure(ErrorCode.NOT_SUPPORTED, "This adapter does not declare the route's feature")


async def no_icon() -> JSONResponse:
    """Answer the icon route: this adapter declares no provider icon."""
    return failure(ErrorCode.NOT_FOUND, "This adapter has no provider icon")


async def _send_target(reader: Reader, body: SendRequest) -> tuple[Room, frozenset[str]] | Response:
    """Validate a send and resolve its room and the outgoing messages already there."""
    if body.reply_to is not None:
        return failure(ErrorCode.NOT_SUPPORTED, "messages.reply is not declared")
    if not 1 <= len(body.text) <= MAX_SEND_LENGTH:
        return failure(ErrorCode.INVALID_REQUEST, "text is empty or longer than maxLength")
    snapshot = await anyio.to_thread.run_sync(reader.snapshot)
    room = next((r for r in snapshot.rooms if r.id == body.room_id), None)
    if room is None:
        return failure(ErrorCode.NOT_FOUND, "No such room in the local cache")
    before = frozenset(m.id for m in snapshot.messages if m.room_id == room.id and m.from_me)
    return room, before


async def perform_send(
    reader: Reader, sender: Sender, line: WriteLine, body: SendRequest
) -> Response:
    """Send text into an existing room, answering the message the client stored.

    The room must be in the cache; the write line admits it or refuses; the sender
    submits to the client's own window; the read-back confirms what was stored. A
    send the read-back cannot confirm is uncertain, answered INTERNAL, not retried.
    """
    resolved = await _send_target(reader, body)
    if isinstance(resolved, Response):
        return resolved
    room, before = resolved
    try:
        async with line.admit():
            await sender.submit(room, body.text)
            stored = await confirm(
                reader, room.id, body.text, before, deadline_seconds=SEND_CONFIRM_SECONDS
            )
    except SendError as refusal:
        return failure(refusal.code, refusal.message)
    if stored is None:
        return failure(ErrorCode.INTERNAL, "submitted but not confirmed")
    answer = SendAnswer(message=stored)
    return JSONResponse(
        content=answer.model_dump(by_alias=True, exclude_none=True, mode="json"),
        headers={"cache-control": "no-store"},
    )


def _route_refusals(
    app: FastAPI, guard: list[params.Depends], routes: tuple[tuple[str, str], ...]
) -> None:
    """Route every documented operation this adapter does not perform, behind the token."""
    app.add_api_route("/icon", no_icon, methods=["GET"], dependencies=guard)
    for method, path in routes:
        app.add_api_route(path, not_supported, methods=[method], dependencies=guard)


def create_app(token: str, reader: Reader | None = None, sender: Sender | None = None) -> FastAPI:
    """Build the adapter; every route but /health requires the caller's token."""
    if len(token.encode("utf-8")) < MIN_TOKEN_BYTES:
        raise ConfigurationError
    backend = reader if reader is not None else Reader()
    slots = StreamSlots(limit=MAX_STREAMS)
    write_line = WriteLine()
    capabilities = declaration(can_send=sender is not None)
    expected = hashlib.sha256(token.encode("utf-8")).digest()
    guard = [params.Depends(_require_token(expected))]
    app = FastAPI(
        title="ChatPlus tsuba adapter",
        version=ADAPTER_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    _install_error_handlers(app)

    @app.get("/health")
    def health() -> Health:
        return Health()

    @app.get("/capabilities", dependencies=guard)
    def describe() -> Capabilities:
        return capabilities

    @app.get("/rooms", dependencies=guard, response_model_exclude_none=True)
    def rooms() -> RoomsAnswer:
        return backend.rooms()

    @app.get("/messages", dependencies=guard, response_model_exclude_none=True)
    def messages(
        room_id: Annotated[str, Query(alias="roomId", min_length=1, max_length=MAX_ID_LENGTH)],
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
        before: Annotated[str | None, Query(min_length=1, max_length=MAX_ID_LENGTH)] = None,
    ) -> MessagesAnswer:
        return backend.messages(room_id, limit, before)

    @app.get("/stream", dependencies=guard)
    async def stream(since: Annotated[list[str] | None, Query()] = None) -> Response:
        return await open_stream(backend, slots, since or [])

    if sender is not None:
        active = sender

        @app.post("/send", dependencies=guard)
        async def send(body: SendRequest) -> Response:
            return await perform_send(backend, active, write_line, body)

    refusals = UNSUPPORTED if sender is not None else (*UNSUPPORTED, ("POST", "/send"))
    _route_refusals(app, guard, refusals)
    return app
