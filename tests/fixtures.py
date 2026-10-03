# Copyright (c) 2026 chatplus-core contributors
"""Build independent synthetic Hive values and encrypted local boxes.

Field names and value shapes follow the structure observed in a real cache
(`Recipients` a list, `From` a string, `Direction` `In`/`Out`); every value is
synthetic.
"""

import struct
from pathlib import Path

from chatplus_core.storage import Profile
from chatplus_core.values import Record, Value
from tests.test_hive import KEY, frame


def sized(tag: int, items: list[bytes]) -> bytes:
    return bytes([tag]) + len(items).to_bytes(4, "little") + b"".join(items)


def encode_scalar(value: Value) -> bytes:
    match value:
        case None:
            return b"\0"
        case bool():
            return bytes([3, int(value)])
        case int():
            return b"\x01" + struct.pack("<d", value)
        case str():
            payload = value.encode("utf-8")
            return b"\x04" + len(payload).to_bytes(4, "little") + payload
        case _:
            raise TypeError(type(value).__name__)


def encode(value: Value) -> bytes:
    match value:
        case list():
            return sized(0x0A, [encode(item) for item in value])
        case dict():
            return sized(0x0B, [encode(key) + encode(item) for key, item in value.items()])
        case Record(tag=18, fields={0: int() as millis, 1: bool() as utc}):
            return bytes([18]) + struct.pack("<d", millis) + bytes([int(utc)])
        case Record():
            return bytes([value.tag, len(value.fields)]) + b"".join(
                bytes([index]) + encode(item) for index, item in value.fields.items()
            )
        case _:
            return encode_scalar(value)


def profile_at(directory: Path) -> Profile:
    folders = [
        Record(
            37,
            {
                0: {"resourceURL": "https://synthetic.invalid/room/a"},
                3: {
                    "Name": "same title",
                    "Date": "2026-01-01T02:00:00Z",
                    "Recipients": ["sip:one@synthetic.invalid"],
                },
            },
        ),
        Record(
            37,
            {
                0: {"resourceURL": "https://synthetic.invalid/room/b"},
                3: {
                    "Name": "same title",
                    "Date": "2026-01-01T04:00:00Z",
                    "Recipients": ["sip:two@synthetic.invalid", "sip:three@synthetic.invalid"],
                },
            },
        ),
    ]
    messages = [
        Record(
            38,
            {
                0: {"parentFolder": "https://synthetic.invalid/room/a"},
                1: "m-old",
                6: {
                    "Date": "2026-01-01T01:00:00Z",
                    "TextContent": "old",
                    "Direction": "In",
                    "From": "sip:one@synthetic.invalid",
                },
            },
        ),
        Record(
            38,
            {
                0: {"parentFolder": "https://synthetic.invalid/room/a"},
                1: "m-new",
                6: {
                    "Date": "2026-01-01T02:00:00Z",
                    "TextContent": "new",
                    "Direction": "Out",
                    "From": "sip:me@synthetic.invalid",
                    "Content-Type": "text/plain",
                },
            },
        ),
        Record(
            38,
            {
                0: {"parentFolder": "https://synthetic.invalid/room/b"},
                1: "m-rich",
                6: {
                    "Date": "2026-01-01T03:00:00Z",
                    "MessageBody": "<rich/>",
                    "Direction": "In",
                    "From": "sip:two@synthetic.invalid",
                },
            },
        ),
        Record(
            38,
            {
                0: {"parentFolder": "https://synthetic.invalid/room/b"},
                1: "m-img",
                6: {
                    "Date": "2026-01-01T04:00:00Z",
                    "Content-Type": "image/jpeg",
                    "Direction": "In",
                    "From": "sip:three@synthetic.invalid",
                },
            },
        ),
    ]
    _ = (directory / "folder.hive").write_bytes(
        frame("version", encode(1))
        + b"".join(frame(f"folder-{i}", encode(folder)) for i, folder in enumerate(folders))
    )
    _ = (directory / "object.hive").write_bytes(
        frame("version", encode(1))
        + b"".join(frame(str(message.fields[1]), encode(message)) for message in reversed(messages))
    )
    return Profile(directory, KEY)
