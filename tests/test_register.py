# Copyright (c) 2026 chatplus-core contributors
"""Registration with saya, against a local stand-in that answers like saya."""

import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast, final

import pytest

from chatplus_core.register import Answer, Registrar, Registration, registration_from_env

SAYA_TOKEN = "synthetic-saya-register-token-0123456789"  # noqa: S105 - synthetic
ADAPTER_TOKEN = "synthetic-adapter-token-0123456789abcdef"  # noqa: S105 - synthetic


@final
class FakeSaya:
    """A register listener that answers like saya, including its refusals."""

    def __init__(self, status: int = 200, body: object = None) -> None:
        self.seen: list[dict[str, object]] = []
        self.arrived: threading.Semaphore = threading.Semaphore(0)
        payload = json.dumps({"interval": 30, "ttl": 90} if body is None else body).encode()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                outer.seen.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "body": cast("object", json.loads(self.rfile.read(length) or b"{}")),
                    },
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                _ = self.wfile.write(payload)
                outer.arrived.release()

            def do_POST(self) -> None:
                self._answer()

            def do_DELETE(self) -> None:
                self._answer()

        self.server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url: str = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def saya_at(status: int = 200, body: object = None) -> Iterator[FakeSaya]:
    saya = FakeSaya(status, body)
    yield saya
    saya.close()


@pytest.fixture
def saya() -> Iterator[FakeSaya]:
    yield from saya_at()


def registration(url: str) -> Registration:
    return Registration(
        register_url=url,
        register_token=SAYA_TOKEN,
        id="chatplus",
        url="https://pc.example.ts.net:8767",
        token=ADAPTER_TOKEN,
    )


def test_off_unless_both_address_and_token_are_given() -> None:
    assert registration_from_env({}, port=8767, token=ADAPTER_TOKEN) is None
    only_url = {"SAYA_REGISTER_URL": "http://127.0.0.1:8798"}
    assert registration_from_env(only_url, port=8767, token=ADAPTER_TOKEN) is None
    env = {"SAYA_REGISTER_URL": "http://127.0.0.1:8798/", "SAYA_REGISTER_TOKEN": SAYA_TOKEN}
    assert registration_from_env(env, port=8767, token=ADAPTER_TOKEN) == Registration(
        "http://127.0.0.1:8798", SAYA_TOKEN, "chatplus", "http://127.0.0.1:8767", ADAPTER_TOKEN
    )
    advertised = registration_from_env(
        {**env, "SAYA_ADVERTISE_URL": "https://pc.example.ts.net:8767"},
        port=8767,
        token=ADAPTER_TOKEN,
    )
    assert advertised is not None
    assert advertised.url == "https://pc.example.ts.net:8767"


def test_announces_with_saya_token_and_says_goodbye(saya: FakeSaya) -> None:
    logs: list[str] = []
    registrar = Registrar(registration(saya.url), log=logs.append)
    assert registrar.beat() == 30.0
    registrar.stop()
    assert saya.seen == [
        {
            "method": "POST",
            "path": "/register",
            "auth": f"Bearer {SAYA_TOKEN}",
            "body": {
                "kind": "adapter",
                "id": "chatplus",
                "url": "https://pc.example.ts.net:8767",
                "token": ADAPTER_TOKEN,
            },
        },
        {
            "method": "DELETE",
            "path": "/register",
            "auth": f"Bearer {SAYA_TOKEN}",
            "body": {"kind": "adapter", "id": "chatplus"},
        },
    ]
    assert logs == ["registered chatplus with saya as https://pc.example.ts.net:8767"]


def test_heartbeat_repeats_until_stopped() -> None:
    for saya in saya_at(body={"interval": 0.01, "ttl": 90}):
        registrar = Registrar(registration(saya.url), log=lambda _: None).start()
        for _ in range(3):
            assert saya.arrived.acquire(timeout=5), "no heartbeat within 5s"
        registrar.stop()
        assert sum(entry["method"] == "POST" for entry in saya.seen) >= 3
        assert saya.seen[-1]["method"] == "DELETE"


def test_refusal_and_unreachable_are_logged_once_without_tokens() -> None:
    refusal = {"error": {"code": "UNAUTHORIZED", "message": "A valid bearer token is required"}}
    logs: list[str] = []
    for saya in saya_at(401, refusal):
        refused = Registrar(registration(saya.url), log=logs.append)
        _ = refused.beat()
        _ = refused.beat()
    assert logs == [
        "saya registration of chatplus refused with 401 UNAUTHORIZED; retrying every 30s"
    ]

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = cast("tuple[str, int]", probe.getsockname())[1]
    dead_logs: list[str] = []
    dead = Registrar(registration(f"http://127.0.0.1:{port}"), log=dead_logs.append)
    assert dead.beat() == 30.0
    assert len(dead_logs) == 1
    assert dead_logs[0].startswith("saya registration of chatplus unreachable")
    for line in logs + dead_logs:
        assert SAYA_TOKEN not in line
        assert ADAPTER_TOKEN not in line


def test_recovery_is_reported() -> None:
    answers: list[Answer] = [(503, {}), (200, {"interval": 30})]
    logs: list[str] = []

    def send(
        _method: str, _url: str, _token: str, _body: dict[str, str], _timeout: float
    ) -> Answer:
        return answers.pop(0)

    registrar = Registrar(registration("http://127.0.0.1:1"), send=send, log=logs.append)
    _ = registrar.beat()
    _ = registrar.beat()
    assert logs == [
        "saya registration of chatplus refused with 503; retrying every 30s",
        "registered chatplus with saya as https://pc.example.ts.net:8767",
    ]
