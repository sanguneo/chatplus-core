# Copyright (c) 2026 chatplus-core contributors
"""Start the local service with an explicit token and loopback binding."""

import os
from typing import Final

import uvicorn

from chatplus_core.api import MAX_STREAMS, create_app
from chatplus_core.login import apply_env_file, load_lock_password
from chatplus_core.reader import Reader
from chatplus_core.winclient import ClientWindow, WindowSender

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
    # Fill any missing settings (token, port, lock password) from a gitignored .env.
    apply_env_file()
    # Sending drives the running client window; the text sender shares the reader
    # so it resolves a room's mobile number from the same cache the reads use.
    window = ClientWindow(load_lock_password())
    reader = Reader()
    app = create_app(
        os.environ.get("CHATPLUS_TOKEN", ""),
        reader=reader,
        sender=WindowSender(reader, window),
        uploader=window,
    )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=resolve_port(),
        access_log=False,
        # Above the stream limit, so a ninth stream gets BUSY and reads still answer.
        limit_concurrency=4 * MAX_STREAMS,
    )


if __name__ == "__main__":
    main()
