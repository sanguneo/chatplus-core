# Copyright (c) 2026 chatplus-core contributors
"""Start the local service with an explicit token and loopback binding."""

import os
import platform
from typing import Final

import uvicorn

from chatplus_core.api import MAX_STREAMS, create_app
from chatplus_core.login import apply_env_file, load_lock_password
from chatplus_core.macclient import STAGING, MacClient
from chatplus_core.reader import Reader
from chatplus_core.register import Registrar, registration_from_env
from chatplus_core.sender import Sender
from chatplus_core.upload import Uploader
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


def writers(reader: Reader) -> tuple[Sender | None, Uploader | None]:
    """Return this platform's window drivers; a platform without one answers reads only."""
    if platform.system() == "Windows":
        # The text sender shares the reader so it resolves a room's mobile number
        # from the same cache the reads use.
        window = ClientWindow(load_lock_password())
        return WindowSender(reader, window), window
    if platform.system() == "Darwin":
        client = MacClient(load_lock_password())
        return WindowSender(reader, client), client
    return None, None


def main() -> None:
    """Keep credentials out of command-line arguments and request logs."""
    # Fill any missing settings (token, port, lock password) from a gitignored .env.
    apply_env_file()
    reader = Reader()
    sender, uploader = writers(reader)
    token = os.environ.get("CHATPLUS_TOKEN", "")
    port = resolve_port()
    app = create_app(
        token,
        reader=reader,
        sender=sender,
        uploader=uploader,
        # The sandboxed macOS client's open sheet may read Downloads; Windows uses temp.
        staging=STAGING if platform.system() == "Darwin" else None,
    )
    # Opt-in: with SAYA_REGISTER_URL and SAYA_REGISTER_TOKEN set, saya learns this adapter itself.
    registration = registration_from_env(os.environ, port=port, token=token)
    registrar = Registrar(registration).start() if registration else None
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            # Above the stream limit, so a ninth stream gets BUSY and reads still answer.
            limit_concurrency=4 * MAX_STREAMS,
        )
    finally:
        if registrar is not None:
            registrar.stop()


if __name__ == "__main__":
    main()
