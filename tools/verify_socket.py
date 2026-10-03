# Copyright (c) 2026 chatplus-core contributors
"""Exercise the real CLI server over loopback TCP and print only aggregate results."""

import json
import os
import secrets
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import IO

import httpx2

from chatplus_core.models import RoomsAnswer

PORT_SCAN_START = 8801
PORT_SCAN_END = 8900
READY_TIMEOUT_SECONDS = 20
SHUTDOWN_TIMEOUT_SECONDS = 10


class StartupError(Exception):
    """The owned server did not reach its listening state."""


def port_is_free(port: int) -> bool:
    """Report whether a chosen loopback port can currently be bound."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def pick_free_port() -> int:
    """Choose a known loopback port by test-binding explicit candidates."""
    for port in range(PORT_SCAN_START, PORT_SCAN_END):
        if port_is_free(port):
            return port
    raise StartupError


def await_ready(stream: IO[str], marker: str, collected: list[str]) -> bool:
    """Block on the child's stdout until the marker line or end of stream."""
    while True:
        line = stream.readline()
        if line == "":
            return False
        collected.append(line.rstrip())
        if marker in line:
            return True


def main() -> None:
    """Launch the real entry point on its own port and verify auth over TCP."""
    token = secrets.token_urlsafe(32)
    port = pick_free_port()
    environment = dict(os.environ, CHATPLUS_TOKEN=token, CHATPLUS_PORT=str(port))
    process: subprocess.Popen[str] = subprocess.Popen(
        [sys.executable, "-m", "chatplus_core"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    with process:
        stdout = process.stdout
        if stdout is None:
            process.terminate()
            raise StartupError
        collected: list[str] = []
        marker = f"Uvicorn running on http://127.0.0.1:{port}"
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                signal = executor.submit(await_ready, stdout, marker, collected)
                try:
                    if not signal.result(timeout=READY_TIMEOUT_SECONDS):
                        print(json.dumps({"startup": collected}))
                        raise StartupError
                except TimeoutError:
                    process.terminate()
                    raise
            with httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
                health = client.get("/health")
                denied = client.get("/rooms")
                allowed = client.get(
                    "/rooms",
                    headers={"Authorization": f"Bearer {token}"},
                )
                _ = allowed.raise_for_status()
                rooms = RoomsAnswer.model_validate_json(allowed.content)
                print(
                    json.dumps(
                        {
                            "transport": "loopback-tcp",
                            "port": port,
                            "healthStatus": health.status_code,
                            "unauthenticatedStatus": denied.status_code,
                            "authenticatedStatus": allowed.status_code,
                            "totalRooms": len(rooms.rooms),
                        }
                    )
                )
        finally:
            if process.poll() is None:
                process.terminate()
            _ = process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)


if __name__ == "__main__":
    main()
