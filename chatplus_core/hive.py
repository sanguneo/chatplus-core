# Copyright (c) 2026 chatplus-core contributors
"""Strict, read-only Hive v2 frames; CRC is not cryptographic authentication."""

import hashlib
import struct
import zlib
from enum import StrEnum
from typing import Final

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAX_INPUT_BYTES: Final = 64 * 1024 * 1024
KEY_BYTES: Final = 32
BLOCK_BYTES: Final = 16
WORD_BYTES: Final = 4
MIN_FRAME_BYTES: Final = 9
STRING_KEY_OFFSET: Final = 6
INTEGER_KEY_END: Final = 9


class HiveIssue(StrEnum):
    """Specific failures of the binary storage contract."""

    SIZE = "Hive input exceeds 64 MiB"
    KEY = "Hive AES key must contain 32 bytes"
    LENGTH = "Invalid frame length"
    CRC = "Frame CRC mismatch"
    KEY_BOUNDS = "Truncated frame key"
    KEY_UTF8 = "Invalid UTF-8 key"
    KEY_TAG = "Unsupported key tag"
    CIPHER = "Invalid IV/ciphertext length"
    PADDING = "Invalid PKCS7 padding"


class HiveFormatError(ValueError):
    """A malformed frame with a non-sensitive byte offset."""

    def __init__(self, issue: HiveIssue, offset: int = 0) -> None:
        """Format only structural information, never input bytes."""
        self.issue: HiveIssue = issue
        super().__init__(f"{issue} at offset {offset}")


def frame_key(frame: bytes) -> tuple[str | int, int]:
    """Decode a bounded frame key, keeping integer and string keys distinct."""
    end = len(frame) - WORD_BYTES
    match frame[WORD_BYTES]:
        case 0:
            if end < INTEGER_KEY_END:
                raise HiveFormatError(HiveIssue.KEY_BOUNDS)
            return int.from_bytes(frame[5:INTEGER_KEY_END], "little"), INTEGER_KEY_END
        case 1:
            if end < STRING_KEY_OFFSET:
                raise HiveFormatError(HiveIssue.KEY_BOUNDS)
            start = STRING_KEY_OFFSET + frame[5]
            if start > end:
                raise HiveFormatError(HiveIssue.KEY_BOUNDS)
            try:
                return frame[STRING_KEY_OFFSET:start].decode("utf-8"), start
            except UnicodeDecodeError as exc:
                raise HiveFormatError(HiveIssue.KEY_UTF8) from exc
        case _:
            raise HiveFormatError(HiveIssue.KEY_TAG)


def decrypt_value(encrypted: bytes, key: bytes) -> bytes:
    """Validate CBC framing and padding before returning opaque value bytes."""
    if len(encrypted) < BLOCK_BYTES * 2 or (len(encrypted) - BLOCK_BYTES) % BLOCK_BYTES:
        raise HiveFormatError(HiveIssue.CIPHER)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(encrypted[:BLOCK_BYTES])).decryptor()
    padded = decryptor.update(encrypted[BLOCK_BYTES:]) + decryptor.finalize()
    padding = padded[-1]
    if not 1 <= padding <= BLOCK_BYTES or padded[-padding:] != bytes([padding]) * padding:
        raise HiveFormatError(HiveIssue.PADDING)
    return padded[:-padding]


def decode_frames(blob: bytes, key: bytes) -> dict[str | int, bytes]:
    """Replay replacements and tombstones, failing the whole read on corruption."""
    if len(blob) > MAX_INPUT_BYTES:
        raise HiveFormatError(HiveIssue.SIZE)
    if len(key) != KEY_BYTES:
        raise HiveFormatError(HiveIssue.KEY)
    seed = zlib.crc32(hashlib.sha256(key).digest())
    values: dict[str | int, bytes] = {}
    offset = 0
    while offset < len(blob):
        if len(blob) - offset < WORD_BYTES:
            raise HiveFormatError(HiveIssue.LENGTH, offset)
        length = struct.unpack_from("<I", blob, offset)[0]
        if length < MIN_FRAME_BYTES or length > len(blob) - offset:
            raise HiveFormatError(HiveIssue.LENGTH, offset)
        frame = blob[offset : offset + length]
        expected = int.from_bytes(frame[-WORD_BYTES:], "little")
        if zlib.crc32(frame[:-WORD_BYTES], seed) != expected:
            raise HiveFormatError(HiveIssue.CRC, offset)
        identity, start = frame_key(frame)
        encrypted = frame[start:-WORD_BYTES]
        if encrypted:
            values[identity] = decrypt_value(encrypted, key)
        else:
            _ = values.pop(identity, None)
        offset += length
    return values
