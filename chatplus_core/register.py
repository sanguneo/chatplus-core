# Copyright (c) 2026 chatplus-core contributors
"""Announce this adapter to saya (saya `docs/register.md`, version 1).

saya holds one register token and no adapter address. Given saya's register
address and token, this adapter tells saya where it is and which bearer token
to present, repeats that every interval saya answers with, and says goodbye on
a clean stop. Registration is opt-in and never a condition of serving: a
refusal or an unreachable saya is logged once, without either token, and tried
again on the next interval. The same protocol as nichirin's
`kakaocommon/register.py` and tsuba's serve-kit.
"""

import json
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeAlias, cast, final

if TYPE_CHECKING:
    from http.client import HTTPResponse

ADAPTER_ID: Final = "chatplus"
DEFAULT_INTERVAL_SECONDS: Final = 30.0
REQUEST_TIMEOUT_SECONDS: Final = 10.0
GOODBYE_TIMEOUT_SECONDS: Final = 3.0
SUCCESS_MIN: Final = 200
SUCCESS_MAX: Final = 300

Answer: TypeAlias = tuple[int, object]
Send: TypeAlias = Callable[[str, str, str, dict[str, str], float], Answer]

_UNSAID: Final = object()


@dataclass(frozen=True)
class Registration:
    """Where saya listens, and what to tell it about this adapter."""

    register_url: str
    register_token: str
    id: str
    url: str
    token: str


def registration_from_env(env: Mapping[str, str], *, port: int, token: str) -> Registration | None:
    """Turn on with `SAYA_REGISTER_URL` and `SAYA_REGISTER_TOKEN`; else None.

    `SAYA_ADVERTISE_URL` is where saya reaches this adapter; without it, this
    machine's loopback, which is right only when saya runs here too.
    """
    register_url = env.get("SAYA_REGISTER_URL", "")
    register_token = env.get("SAYA_REGISTER_TOKEN", "")
    if not register_url or not register_token:
        return None
    return Registration(
        register_url=register_url.rstrip("/"),
        register_token=register_token,
        id=ADAPTER_ID,
        url=env.get("SAYA_ADVERTISE_URL") or f"http://127.0.0.1:{port}",
        token=token,
    )


def _decode(raw: bytes) -> object:
    try:
        return cast("object", json.loads(raw or b"{}"))
    except ValueError:
        return {}


def _send(method: str, url: str, token: str, body: dict[str, str], timeout: float) -> Answer:
    request = urllib.request.Request(  # noqa: S310 - the operator's configured saya address
        url,
        data=json.dumps(body).encode("utf-8"),
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with cast("HTTPResponse", urllib.request.urlopen(request, timeout=timeout)) as answer:  # noqa: S310
            return answer.status, _decode(answer.read())
    except urllib.error.HTTPError as refused:
        return refused.code, _decode(refused.read())


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)  # noqa: T201 - the service's own log line


def _field(body: object, key: str) -> object:
    return cast("dict[str, object]", body).get(key) if isinstance(body, dict) else None


def _interval(body: object) -> float:
    answered = _field(body, "interval")
    if isinstance(answered, int | float) and answered > 0:
        return float(answered)
    return DEFAULT_INTERVAL_SECONDS


def _code(body: object) -> str:
    code = _field(_field(body, "error"), "code")
    return code if isinstance(code, str) else ""


@final
class Registrar:
    """Keep one registration alive on a daemon thread until `stop`."""

    def __init__(
        self,
        registration: Registration,
        *,
        send: Send = _send,
        log: Callable[[str], None] = _stderr,
    ) -> None:
        """Hold the registration; nothing is sent until `beat` or `start`."""
        self._registration = registration
        self._send = send
        self._log = log
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._reported: object = _UNSAID

    @property
    def _endpoint(self) -> str:
        return f"{self._registration.register_url}/register"

    def beat(self) -> float:
        """Register once; return the seconds until the next heartbeat."""
        r = self._registration
        interval = DEFAULT_INTERVAL_SECONDS
        problem: str | None = None
        try:
            status, body = self._send(
                "POST",
                self._endpoint,
                r.register_token,
                {"kind": "adapter", "id": r.id, "url": r.url, "token": r.token},
                REQUEST_TIMEOUT_SECONDS,
            )
            if SUCCESS_MIN <= status < SUCCESS_MAX:
                interval = _interval(body)
            else:
                problem = f"refused with {status} {_code(body)}".strip()
        except (OSError, ValueError) as error:
            problem = f"unreachable ({type(error).__name__})"
        if problem != self._reported:
            self._log(
                f"saya registration of {r.id} {problem}; retrying every {interval:g}s"
                if problem
                else f"registered {r.id} with saya as {r.url}",
            )
            self._reported = problem
        return interval

    def _loop(self) -> None:
        interval = self.beat()
        while not self._stopping.wait(interval):
            interval = self.beat()

    def start(self) -> "Registrar":
        """Register now and keep doing so on a daemon thread."""
        self._thread = threading.Thread(target=self._loop, name="saya-register", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the heartbeat and take the registration back, best effort."""
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(REQUEST_TIMEOUT_SECONDS)
        r = self._registration
        try:
            _ = self._send(
                "DELETE",
                self._endpoint,
                r.register_token,
                {"kind": "adapter", "id": r.id},
                GOODBYE_TIMEOUT_SECONDS,
            )
        except (OSError, ValueError):
            self._log(f"saya did not take back {r.id}; it lapses after its ttl")
