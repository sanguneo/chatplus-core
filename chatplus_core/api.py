# Copyright (c) 2026 chatplus-core contributors
"""Authenticated loopback API; only implemented read capabilities are exposed."""

import binascii
import hashlib
import hmac
from typing import Annotated, Final, Literal

from cryptography.exceptions import InvalidTag
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from chatplus_core.hive import HiveFormatError
from chatplus_core.models import Messages, Model, Rooms
from chatplus_core.reader import Reader, RoomNotFoundError
from chatplus_core.storage import StorageError
from chatplus_core.values import ValueFormatError

MIN_TOKEN_BYTES: Final = 32


class ConfigurationError(Exception):
    """The local API cannot start with the supplied configuration."""

    def __init__(self) -> None:
        """Describe the startup requirement without echoing the token."""
        super().__init__(f"CHATPLUS_TOKEN must contain at least {MIN_TOKEN_BYTES} bytes")


class Capabilities(Model):
    """No send, history completeness, attachments or stream claims."""

    ok: Literal[True] = True
    provider: Literal["chatplus"] = "chatplus"
    mode: Literal["read-only"] = "read-only"
    features: tuple[str, ...] = ("rooms.read", "messages.read")


class ErrorDetail(Model):
    """A stable error code and a non-sensitive explanation."""

    code: str
    message: str


class Failure(Model):
    """The shared failure envelope."""

    ok: Literal[False] = False
    error: ErrorDetail


def failure(status: int, code: str, message: str) -> JSONResponse:
    """Serialize failures without including exception text or request inputs."""
    payload = Failure(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status, content=payload.model_dump())


async def http_error(_request: Request, exc: Exception) -> JSONResponse:
    """Keep HTTP errors in the shared envelope."""
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    match exc.status_code:
        case 401:
            return failure(401, "UNAUTHORIZED", "Bearer token required")
        case 404:
            return failure(404, "NOT_FOUND", "Route not found")
        case 405:
            return failure(405, "METHOD_NOT_ALLOWED", "Method not allowed")
        case _:
            return failure(exc.status_code, "HTTP_ERROR", "HTTP request refused")


async def bad_request(_request: Request, _exc: Exception) -> JSONResponse:
    """Avoid reflecting private query arguments in validation failures."""
    return failure(400, "BAD_REQUEST", "Invalid query parameters")


async def missing_room(_request: Request, _exc: Exception) -> JSONResponse:
    """Distinguish a missing room from an empty conversation."""
    return failure(404, "NOT_FOUND", "Room is absent from the current local cache")


async def unavailable(_request: Request, _exc: Exception) -> JSONResponse:
    """Withhold filesystem paths and operating-system error details."""
    return failure(503, "STORAGE_UNAVAILABLE", "Local cache could not be read")


async def unsupported(_request: Request, _exc: Exception) -> JSONResponse:
    """Reject unknown or corrupt storage instead of returning partial data."""
    return failure(503, "UNSUPPORTED_FORMAT", "Local cache format could not be decoded")


def create_app(token: str, reader: Reader | None = None) -> FastAPI:
    """Build an app whose every exposed route requires the caller's token."""
    if len(token.encode("utf-8")) < MIN_TOKEN_BYTES:
        raise ConfigurationError
    expected = hashlib.sha256(token.encode("utf-8")).digest()
    backend = reader if reader is not None else Reader()
    bearer = HTTPBearer(auto_error=False)

    def authenticate(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if credentials is None:
            raise HTTPException(status_code=401)
        actual = hashlib.sha256(credentials.credentials.encode("utf-8")).digest()
        if not hmac.compare_digest(expected, actual):
            raise HTTPException(status_code=401)

    app = FastAPI(
        title="ChatPlus local core",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(authenticate)],
        responses={
            400: {"model": Failure},
            401: {"model": Failure},
            404: {"model": Failure},
            405: {"model": Failure},
            503: {"model": Failure},
            "4XX": {"model": Failure},
        },
    )

    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, bad_request)
    app.add_exception_handler(RoomNotFoundError, missing_room)
    handlers = {
        OSError: unavailable,
        StorageError: unavailable,
        InvalidTag: unavailable,
        ValueFormatError: unsupported,
        HiveFormatError: unsupported,
        UnicodeError: unsupported,
        binascii.Error: unsupported,
    }
    for kind, handler in handlers.items():
        app.add_exception_handler(kind, handler)

    @app.get("/capabilities")
    def capabilities() -> Capabilities:
        return Capabilities()

    @app.get("/rooms")
    def rooms(limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> Rooms:
        return backend.rooms(limit)

    @app.get("/messages")
    def messages(
        room_id: Annotated[str, Query(alias="roomId", pattern="^[a-f0-9]{64}$")],
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> Messages:
        return backend.messages(room_id, limit)

    @app.get("/openapi.json", include_in_schema=False)
    def specification() -> JSONResponse:
        return JSONResponse(app.openapi())

    return app
