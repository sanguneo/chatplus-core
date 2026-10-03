# Copyright (c) 2026 chatplus-core contributors
import struct

import pytest

from chatplus_core.values import Record, ValueFormatError, decode_value


def text(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return b"\x04" + struct.pack("<I", len(encoded)) + encoded


def test_generated_adapter_preserves_field_numbers() -> None:
    # Given a synthetic object with a map and a string field.
    mapping = b"\x0b\x01\x00\x00\x00" + text("subject") + text("synthetic")
    payload = bytes([38, 2, 0]) + mapping + bytes([7]) + text("body")
    # When the full payload is decoded.
    result = decode_value(payload)
    # Then wire field numbers and Unicode text survive.
    assert result == Record(38, {0: {"subject": "synthetic"}, 7: "body"})


def test_unicode_string_list() -> None:
    # Given a Hive string list without per-element type tags.
    strings = ["테스트", "sample"]
    payload = b"\x09\x02\x00\x00\x00" + b"".join(text(s)[1:] for s in strings)
    # When decoded.
    result = decode_value(payload)
    # Then each complete string is retained.
    assert result == strings


def test_enum_does_not_consume_neighboring_fields() -> None:
    # Given an enum followed by ordinary fields in the enclosing record.
    payload = bytes([38, 3, 3, 42, 7, 4]) + text("body") + bytes([11, 61, 0])
    # When decoded.
    result = decode_value(payload)
    # Then the enum byte is not treated as a field count.
    assert result == Record(38, {3: Record(42, {0: 7}), 4: "body", 11: Record(61, {0: 0})})


def test_hive_datetime_with_timezone_is_decoded() -> None:
    # Given Hive's built-in DateTimeWithTimezone value in a record's field map,
    # where the client stores a send time for a message sent from the PC.
    stamp = bytes([18]) + struct.pack("<d", 1_767_243_600_000) + bytes([1])
    payload = bytes([38, 1, 10]) + b"\x0b\x01\x00\x00\x00" + text("sendDate") + stamp
    # When decoded.
    result = decode_value(payload)
    # Then the milliseconds and the UTC flag are kept and no byte trails.
    assert result == Record(38, {10: {"sendDate": Record(18, {0: 1_767_243_600_000, 1: True})}})


@pytest.mark.parametrize(
    "payload",
    [
        b"\x04\x04\x00\x00\x00x",
        b"\x00\x00",
        bytes([255, 0]),
        bytes([38, 2, 0, 0, 0, 0]),
        b"\x09\xff\xff\xff\x7f",
        bytes([18]) + struct.pack("<d", 1.0),
    ],
)
def test_malformed_values_are_rejected(payload: bytes) -> None:
    # Given an incomplete, unknown, duplicate or oversized value.
    # When parsed, then no partial result is returned.
    with pytest.raises(ValueFormatError):
        _ = decode_value(payload)
