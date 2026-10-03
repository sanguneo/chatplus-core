# Copyright (c) 2026 chatplus-core contributors
"""Start the local service with an explicit token and loopback binding."""

import os
from typing import Final

import uvicorn

from chatplus_core.api import create_app

DEFAULT_PORT: Final = 8766
MIN_PORT: Final = 1024
MAX_PORT: Final = 65535


class PortConfigError(ValueError):
    """CHATPLUS_PORT is outside the accepted non-privileged range."""

    def __init__(self) -> None:
        """Report the accepted range without echoing the rejected value."""
        super().__init__(f"CHATPLUS_PORT must be an integer in {MIN_PORT}-{MAX_PORT}")


def resolve_port() -> int:
    """Parse an optional port override, refusing privileged or malformed values."""
    raw = os.environ.get("CHATPLUS_PORT")
    if raw is None or raw == "":
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise PortConfigError from exc
    if not MIN_PORT <= port <= MAX_PORT:
        raise PortConfigError
    return port


def main() -> None:
    """Keep credentials out of command-line arguments and request logs."""
    app = create_app(os.environ.get("CHATPLUS_TOKEN", ""))
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=resolve_port(),
        access_log=False,
        limit_concurrency=8,
    )


if __name__ == "__main__":
    main()
