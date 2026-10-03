# Copyright (c) 2026 chatplus-core contributors
"""Build independent synthetic Hive values and encrypted local boxes."""

import struct
from pathlib import Path

from chatplus_core.storage import Profile
from chatplus_core.values import Record, Value
from tests.test_hive import KEY, frame


def encode(value: Value) -> bytes:
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
        case dict():
            return (
                b"\x0b"
                + len(value).to_bytes(4, "little")
                + b"".join(encode(key) + encode(item) for key, item in value.items())
            )
        case Record():
            return bytes([value.tag, len(value.fields)]) + b"".join(
                bytes([index]) + encode(item) for index, item in value.fields.items()
            )
        case _:
            raise TypeError(type(value).__name__)


def profile_at(directory: Path) -> Profile:
    folders = [
        Record(
            37, {0: {"resourceURL": "https://synthetic.invalid/room/a"}, 3: {"Name": "same title"}}
        ),
        Record(
            37, {0: {"resourceURL": "https://synthetic.invalid/room/b"}, 3: {"Name": "same title"}}
        ),
    ]
    messages = [
        Record(
            38,
            {
                0: {"parentFolder": "https://synthetic.invalid/room/a"},
                1: "m-old",
                6: {"Date": "2026-01-01T01:00:00Z", "TextContent": "old", "Direction": "received"},
            },
        ),
        Record(
            38,
            {
                0: {"parentFolder": "https://synthetic.invalid/room/a"},
                1: "m-new",
                6: {"Date": "2026-01-01T02:00:00Z", "TextContent": "new", "Direction": "sent"},
            },
        ),
        Record(
            38,
            {
                0: {"parentFolder": "https://synthetic.invalid/room/b"},
                1: "m-rich",
                6: {"Date": "2026-01-01T03:00:00Z", "MessageBody": "<rich/>"},
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
