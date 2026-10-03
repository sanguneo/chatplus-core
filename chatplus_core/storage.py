# Copyright (c) 2026 chatplus-core contributors
"""Discover one local profile and read its encrypted stores without writes."""

import base64
import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final, final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_BYTES: Final = 64 * 1024 * 1024
MASTER_KEY_BYTES: Final = 16
HIVE_KEY_BYTES: Final = 32
GCM_HEADER_BYTES: Final = 28


class StorageIssue(StrEnum):
    """Specific local-environment and snapshot failures."""

    KEY = "Client storage key unavailable"
    KEY_FORMAT = "Unsupported storage key format"
    SIZE = "Store exceeds read limit"
    CHANGED = "Client stores changed during read; try again"
    PLATFORM = "ChattingPlus Windows is required"
    PROFILES = "Exactly one local profile is required"
    KEYS = "Exactly one matching cipher key is required"
    TRUNCATED = "Truncated encrypted key"
    HIVE_KEY = "Unsupported Hive key format"
    ORPHAN = "Message references a room absent from this cache view"


class StorageError(Exception):
    """A local profile cannot be read safely."""

    def __init__(self, issue: StorageIssue) -> None:
        """Report only a stable reason, never a path or credential."""
        self.issue: StorageIssue = issue
        super().__init__(str(issue))


@final
class Credential(ctypes.Structure):
    """Windows CREDENTIALW layout."""

    _fields_ = [
        ("flags", wintypes.DWORD),
        ("kind", wintypes.DWORD),
        ("target", wintypes.LPWSTR),
        ("comment", wintypes.LPWSTR),
        ("written", wintypes.FILETIME),
        ("size", wintypes.DWORD),
        ("blob", ctypes.POINTER(ctypes.c_ubyte)),
        ("persist", wintypes.DWORD),
        ("attribute_count", wintypes.DWORD),
        ("attributes", ctypes.c_void_p),
        ("alias", wintypes.LPWSTR),
        ("user", wintypes.LPWSTR),
    ]

    def __init__(self) -> None:
        """Initialize an owned empty structure; CredReadW returns separate memory."""
        super().__init__()
        self.size: int = 0
        self.blob: wintypes.LPBYTE = wintypes.LPBYTE()


def storage_key() -> bytes:
    """Read the observed Flutter key without creating or modifying credentials."""
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(Credential)),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    credential = ctypes.POINTER(Credential)()
    if not api.CredReadW("key_.", 1, 0, ctypes.byref(credential)):
        raise StorageError(StorageIssue.KEY)
    try:
        result = ctypes.string_at(credential.contents.blob, credential.contents.size)
        if len(result) != MASTER_KEY_BYTES:
            raise StorageError(StorageIssue.KEY_FORMAT)
        return result
    finally:
        api.CredFree(credential)


def read_stable(path: Path) -> bytes:
    """Require two equal bounded reads; this is not a multi-file transaction."""
    with path.open("rb") as source:
        first = source.read(MAX_BYTES + 1)
        if len(first) > MAX_BYTES:
            raise StorageError(StorageIssue.SIZE)
        _ = source.seek(0)
        second = source.read(MAX_BYTES + 1)
    if first != second:
        raise StorageError(StorageIssue.CHANGED)
    return first


@dataclass(frozen=True, slots=True)
class Profile:
    """A profile directory and its in-memory key; repr never includes the key."""

    directory: Path
    key: bytes = field(repr=False)


def discover() -> Profile:
    """Refuse multiple profiles or keys instead of guessing the active account."""
    local = os.environ.get("LOCALAPPDATA")
    if os.name != "nt" or local is None:
        raise StorageError(StorageIssue.PLATFORM)
    package = Path(local) / "Packages/com.sktelecom.torchlight_82zr7n7shan6r/LocalCache"
    data = package / "Local/SK Telecom Co., Ltd/ChattingPlus/edb"
    secure = package / "Roaming/SK Telecom Co., Ltd/ChattingPlus"
    profiles = [path for path in data.iterdir() if path.is_dir()]
    if len(profiles) != 1:
        raise StorageError(StorageIssue.PROFILES)
    profile = profiles[0]
    keys: list[Path] = []
    for path in secure.glob(".sK2Nwk*.secure"):
        name = base64.b64decode(path.name[7:-7], validate=True).decode("utf-8")
        if name.startswith(f"cipher_key_{profile.name}_"):
            keys.append(path)
    if len(keys) != 1:
        raise StorageError(StorageIssue.KEYS)
    blob = read_stable(keys[0])
    if len(blob) <= GCM_HEADER_BYTES:
        raise StorageError(StorageIssue.TRUNCATED)
    plaintext = AESGCM(storage_key()).decrypt(blob[:12], blob[28:] + blob[12:28], None)
    key = base64.b64decode(plaintext.rstrip(b"\0"), altchars=b"-_", validate=True)
    if len(key) != HIVE_KEY_BYTES:
        raise StorageError(StorageIssue.HIVE_KEY)
    return Profile(profile, key)
