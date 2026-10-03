# Copyright (c) 2026 chatplus-core contributors
"""Synthetic Hive frames only; no account files or secrets."""

import binascii
import hashlib

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from chatplus_core.hive import MAX_INPUT_BYTES, HiveFormatError, decode_frames

KEY = bytes(range(32))
IV = bytes(range(16))


def seal(body: bytes, key: bytes = KEY) -> bytes:
    """Build framing independently of the reader's struct/zlib implementation."""
    prefix = (len(body) + 8).to_bytes(4, "little") + body
    checksum = binascii.crc32(prefix, binascii.crc32(hashlib.sha256(key).digest()))
    return prefix + checksum.to_bytes(4, "little")


def encrypt_padded(padded: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(KEY), modes.CBC(IV)).encryptor()
    return IV + encryptor.update(padded) + encryptor.finalize()


def frame(key: str | int, value: bytes | None) -> bytes:
    if isinstance(key, int):
        encoded_key = b"\x00" + key.to_bytes(4, "little")
    else:
        text = key.encode("utf-8")
        encoded_key = b"\x01" + bytes([len(text)]) + text
    if value is None:
        return seal(encoded_key)
    count = 16 - len(value) % 16
    return seal(encoded_key + encrypt_padded(value + bytes([count]) * count))


def test_replays_replacements_deletions_and_reinsertion() -> None:
    blob = b"".join(
        [
            frame("room", b"\x81first"),
            frame(7, b"\x00"),
            frame("room", b"\x82second"),
            frame("absent", None),
            frame(7, None),
            frame("room", None),
            frame("room", b"\xfflast"),
        ]
    )
    assert decode_frames(blob, KEY) == {"room": b"\xfflast"}


def test_replacement_keeps_latest_payload() -> None:
    assert decode_frames(frame(1, b"old") + frame(1, b"new"), KEY) == {1: b"new"}


@pytest.mark.parametrize("key", [0, 0xFFFFFFFF, "", "1", "\u00e9\u2603", "a" * 255])
def test_key_types_and_boundaries(key: str | int) -> None:
    assert decode_frames(frame(key, b"\x99opaque"), KEY) == {key: b"\x99opaque"}


def test_integer_and_string_keys_are_distinct() -> None:
    assert decode_frames(frame(1, b"a") + frame("1", b"b"), KEY) == {1: b"a", "1": b"b"}


@pytest.mark.parametrize(
    "payload", [b"", b"\x00", b"x" * 15, b"x" * 16, b"x" * 17, bytes(range(256))]
)
def test_padding_boundaries_and_opaque_values(payload: bytes) -> None:
    assert decode_frames(frame("k", payload), KEY) == {"k": payload}


def test_empty_input() -> None:
    assert decode_frames(b"", KEY) == {}


def test_wrong_key_rejects_even_deletions() -> None:
    for blob in (frame("k", b"x"), frame("k", None)):
        with pytest.raises(HiveFormatError, match="CRC"):
            _ = decode_frames(blob, bytes(reversed(KEY)))


@pytest.mark.parametrize("key", [b"", b"x" * 16, b"x" * 31, b"x" * 33])
def test_invalid_key_length(key: bytes) -> None:
    with pytest.raises(HiveFormatError, match="32 bytes"):
        _ = decode_frames(b"", key)


def test_input_limit() -> None:
    with pytest.raises(HiveFormatError, match="64 MiB"):
        _ = decode_frames(b"\x00" * (MAX_INPUT_BYTES + 1), KEY)


def test_all_nonempty_truncations_fail_even_after_valid_frame() -> None:
    good = frame("first", b"kept")
    following = frame(3, b"payload")
    for end in range(1, len(following)):
        with pytest.raises(HiveFormatError):
            _ = decode_frames(good + following[:end], KEY)


@pytest.mark.parametrize("length", [0, 4, 8, 0xFFFFFFFF])
def test_invalid_frame_length(length: int) -> None:
    with pytest.raises(HiveFormatError, match="frame length"):
        _ = decode_frames(length.to_bytes(4, "little") + b"\x00" * 9, KEY)


def test_every_single_byte_corruption_fails() -> None:
    original = frame("k", b"\x81payload")
    for index in range(len(original)):
        changed = bytearray(original)
        changed[index] ^= 1
        with pytest.raises(HiveFormatError):
            _ = decode_frames(bytes(changed), KEY)


@pytest.mark.parametrize(
    "body",
    [
        b"\x02",
        b"\xff",
        b"\x00",
        b"\x00\x01\x02\x03",
        b"\x01",
        b"\x01\x05abc",
        b"\x01\x01\xff",
        b"\x01\x02\xc0\x80",
    ],
)
def test_malformed_key_with_valid_crc(body: bytes) -> None:
    with pytest.raises(HiveFormatError):
        _ = decode_frames(seal(body), KEY)


@pytest.mark.parametrize("size", [1, 15, 16, 17, 31, 33, 47])
def test_invalid_iv_and_ciphertext_lengths(size: int) -> None:
    with pytest.raises(HiveFormatError, match="IV/ciphertext"):
        _ = decode_frames(seal(b"\x01\x01k" + b"x" * size), KEY)


@pytest.mark.parametrize(
    "padded",
    [
        b"x" * 15 + b"\x00",
        b"x" * 15 + b"\x11",
        b"x" * 14 + b"\x01\x02",
        b"\x0f" + b"\x10" * 15,
    ],
)
def test_malformed_padding_with_valid_crc(padded: bytes) -> None:
    blob = seal(b"\x01\x01k" + encrypt_padded(padded))
    with pytest.raises(HiveFormatError, match="PKCS7"):
        _ = decode_frames(blob, KEY)
