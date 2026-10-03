# Copyright (c) 2026 chatplus-core contributors
"""Decode observed Hive adapters; unsupported schemas fail closed."""

import math
import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypeAlias


class ValueIssue(StrEnum):
    """Failures at the value-to-domain boundary."""

    TRUNCATED = "Truncated value"
    SIZE = "Value exceeds size limit"
    DEPTH = "Value nesting limit exceeded"
    NUMBER = "Invalid Hive integer"
    MAP_KEY = "Unsupported map key"
    TAG = "Unsupported value adapter"
    DUPLICATE = "Duplicate adapter field"
    TRAILING = "Trailing payload bytes"
    MAP = "Expected cached attribute map"
    STRING = "Expected cached string"
    RECORD = "Unexpected box record"
    IDENTITY = "Message identity disagrees with frame key"
    ROOM = "Duplicate room resource"
    DATE = "Invalid or timezone-less cached message date"
    RECIPIENTS = "Expected cached recipient list"


class ValueFormatError(Exception):
    """A typed storage-value refusal without any record content."""

    def __init__(self, issue: ValueIssue) -> None:
        """Keep public diagnostics independent of private stored data."""
        self.issue: ValueIssue = issue
        super().__init__(str(issue))


@dataclass(frozen=True, slots=True)
class Record:
    """Generated adapter fields identified by their wire numbers."""

    tag: int
    fields: dict[int, "Value"]


Value: TypeAlias = (
    bool | int | float | str | bytes | list["Value"] | dict[str | int, "Value"] | Record | None
)
ADAPTERS: Final = frozenset({37, 38, 52, 64, 75, 85, 122, 133, 145})
ENUM_ADAPTERS: Final = frozenset({42, 61})
MAX_ITEMS: Final = 1_000_000
MAX_DEPTH: Final = 32
MAX_SCALAR_TAG: Final = 5
MAX_LIST_TAG: Final = 10
MAP_TAG: Final = 11
OBJECT_TAG: Final = 38


class ValueReader:
    """A mutable cursor owned by one bounded decode."""

    def __init__(self, data: bytes) -> None:
        """Start before the first value tag."""
        self.data: bytes = data
        self.offset: int = 0

    def take(self, size: int) -> bytes:
        """Consume only bytes within the payload."""
        if size < 0 or self.offset + size > len(self.data):
            raise ValueFormatError(ValueIssue.TRUNCATED)
        result = self.data[self.offset : self.offset + size]
        self.offset += size
        return result

    def byte(self) -> int:
        """Consume a tag or field index."""
        return self.take(1)[0]

    def count(self) -> int:
        """Read a bounded collection or byte count."""
        count = int.from_bytes(self.take(4), "little")
        if count > MAX_ITEMS:
            raise ValueFormatError(ValueIssue.SIZE)
        return count

    def string(self) -> str:
        """Read strict length-prefixed UTF-8."""
        return self.take(self.count()).decode("utf-8")

    def number(self) -> float:
        """Decode an eight-byte Hive number."""
        (result,) = struct.unpack("<d", self.take(8))
        return float(result)

    def integer(self) -> int:
        """Refuse fractional or nonfinite integer representations."""
        value = self.number()
        if not math.isfinite(value) or not value.is_integer():
            raise ValueFormatError(ValueIssue.NUMBER)
        return int(value)

    def scalar(self, tag: int) -> Value:
        """Decode scalar wire types."""
        match tag:
            case 0:
                return None
            case 1:
                return self.integer()
            case 2:
                return self.number()
            case 3:
                return self.byte() > 0
            case 4:
                return self.string()
            case 5:
                return self.take(self.count())
            case _:
                raise ValueFormatError(ValueIssue.TAG)

    def sequence(self, tag: int, depth: int) -> list[Value]:
        """Decode homogeneous or tagged lists without conflating encodings."""
        count = self.count()
        match tag:
            case 6:
                return [self.integer() for _ in range(count)]
            case 7:
                return [self.number() for _ in range(count)]
            case 8:
                return [self.byte() > 0 for _ in range(count)]
            case 9:
                return [self.string() for _ in range(count)]
            case 10:
                return [self.read(depth + 1) for _ in range(count)]
            case _:
                raise ValueFormatError(ValueIssue.TAG)

    def mapping(self, depth: int) -> dict[str | int, Value]:
        """Decode a map whose keys are explicitly supported."""
        result: dict[str | int, Value] = {}
        for _ in range(self.count()):
            key = self.read(depth + 1)
            if not isinstance(key, (str, int)) or isinstance(key, bool):
                raise ValueFormatError(ValueIssue.MAP_KEY)
            if key in result:
                raise ValueFormatError(ValueIssue.DUPLICATE)
            result[key] = self.read(depth + 1)
        return result

    def read(self, depth: int = 0) -> Value:
        """Dispatch exactly one supported value."""
        if depth > MAX_DEPTH:
            raise ValueFormatError(ValueIssue.DEPTH)
        tag = self.byte()
        if tag <= MAX_SCALAR_TAG:
            return self.scalar(tag)
        if tag <= MAX_LIST_TAG:
            return self.sequence(tag, depth)
        if tag == MAP_TAG:
            return self.mapping(depth)
        if tag in ENUM_ADAPTERS:
            return Record(tag, {0: self.byte()})
        if tag not in ADAPTERS:
            raise ValueFormatError(ValueIssue.TAG)
        fields: dict[int, Value] = {}
        for _ in range(self.byte()):
            field = self.byte()
            if field in fields:
                raise ValueFormatError(ValueIssue.DUPLICATE)
            fields[field] = self.read(depth + 1)
        return Record(tag, fields)


def decode_value(data: bytes) -> Value:
    """Decode exactly one complete payload."""
    reader = ValueReader(data)
    result = reader.read()
    if reader.offset != len(data):
        raise ValueFormatError(ValueIssue.TRAILING)
    return result
