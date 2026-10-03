# Copyright (c) 2026 chatplus-core contributors
"""tsuba-contract envelope: the failure shape and the code-to-status map.

Mirrors the tsuba serve-kit envelope so this Python adapter answers the same
contract saya pins. The HTTP status and the error code say the same thing twice:
the status for a proxy, the code for the client.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

from fastapi.responses import JSONResponse


class ErrorCode(StrEnum):
    """The tsuba error codes; each maps to exactly one HTTP status."""

    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_REQUEST = "INVALID_REQUEST"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL = "INTERNAL"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    BUSY = "BUSY"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"


STATUS: Final[dict[ErrorCode, int]] = {
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL: 500,
    ErrorCode.NOT_SUPPORTED: 501,
    ErrorCode.BUSY: 503,
    ErrorCode.REAUTH_REQUIRED: 503,
}


def error_body(code: ErrorCode, message: str) -> dict[str, bool | dict[str, str]]:
    """Build the failure envelope; the message never carries account data."""
    return {"ok": False, "error": {"code": code.value, "message": message}}


def failure(
    code: ErrorCode,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Answer the failure envelope with the code's fixed HTTP status."""
    return JSONResponse(
        status_code=STATUS[code],
        content=error_body(code, message),
        headers={"cache-control": "no-store", **(headers or {})},
    )
