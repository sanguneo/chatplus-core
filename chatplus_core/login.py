# Copyright (c) 2026 chatplus-core contributors
"""The owner-supplied lock password for the client's lock mode.

ChatPlus locks the client after an idle period (its own setting, up to 24h).
Reads keep working from the cache, but the window shows only a six-digit
password screen, so a send cannot proceed. With the lock password configured,
the window driver enters it once before a send. Only the lock password is stored: it is read
from the environment (a gitignored `.env` the owner fills), never printed,
logged, or committed; the window driver is a separate seam. Without it the
adapter does nothing new.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

LOCK_PASSWORD_ENV: Final = "CHATPLUS_LOCK_PASSWORD"  # noqa: S105 - env var name, not a secret
ENV_FILE: Final = Path(__file__).resolve().parents[1] / ".env"
QUOTED: Final = 2


@dataclass(frozen=True, slots=True)
class LockPassword:
    """The owner's ChatPlus lock password; repr never includes it."""

    value: str = field(repr=False)


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE `.env`, ignoring blanks, comments and malformed lines."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        trimmed = value.strip()
        if len(trimmed) >= QUOTED and trimmed[0] == trimmed[-1] and trimmed[0] in {'"', "'"}:
            trimmed = trimmed[1:-1]
        if key:
            values[key] = trimmed
    return values


def apply_env_file(path: Path = ENV_FILE, env: dict[str, str] | None = None) -> None:
    """Fill missing environment variables from a `.env`, never overriding what is already set."""
    target = env if env is not None else os.environ
    for key, value in read_env_file(path).items():
        _ = target.setdefault(key, value)


def load_lock_password(env: Mapping[str, str] | None = None) -> LockPassword | None:
    """Return the configured lock password, or None when it is absent: off by default."""
    source = env if env is not None else os.environ
    value = source.get(LOCK_PASSWORD_ENV) or ""
    return LockPassword(value) if value else None
