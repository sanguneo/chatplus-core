# Copyright (c) 2026 chatplus-core contributors
from typing import final

import anyio
import pytest

from chatplus_core.models import Room
from chatplus_core.sender import RoomUnverifiedError
from chatplus_core.winclient import (
    Line,
    WindowSender,
    composer_empty,
    digits,
    has_number,
    ready_to_send,
)


def test_a_number_matches_only_digit_for_digit() -> None:
    lines = [Line(10, "친구"), Line(30, "010-0000-0001")]
    found = has_number(lines, "01000000001")
    assert found is not None
    assert found.top == 30
    assert has_number(lines, "0100000000") is None
    assert has_number(lines, "01000000010") is None
    assert digits("010 0000-0001") == "01000000001"


def shade(value: int, count: int) -> bytes:
    return bytes((value, value, value, 255)) * count


def test_only_a_placeholder_with_no_dark_text_is_an_empty_composer() -> None:
    white, placeholder, caret, text = 250, 200, 118, 30
    assert composer_empty(shade(white, 1000) + shade(placeholder, 80) + shade(caret, 30))
    assert not composer_empty(shade(white, 1000) + shade(placeholder, 80) + shade(text, 5))
    assert not composer_empty(shade(white, 1000) + shade(caret, 30))


def colour(spread: int, count: int) -> bytes:
    half = spread // 2
    return bytes((128 - half, 128, 128 + half, 255)) * count


def test_only_a_filled_send_button_reads_as_ready() -> None:
    assert ready_to_send(colour(120, 1600) + colour(0, 200))
    assert not ready_to_send(colour(0, 1800))
    assert not ready_to_send(colour(120, 100) + colour(0, 1700))


@final
class FakeReader:
    def __init__(self, numbers: dict[str, str | None]) -> None:
        self.numbers: dict[str, str | None] = numbers

    def recipient_numbers(self) -> dict[str, str | None]:
        return self.numbers


@final
class FakeWindow:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, number: str, text: str) -> None:
        self.sent.append((number, text))


def room(room_id: str) -> Room:
    return Room(id=room_id, title="t", kind="direct")


def test_window_sender_resolves_the_number_and_types_the_text() -> None:
    window = FakeWindow()
    sender = WindowSender(FakeReader({"r-1": "01000000000"}), window)
    anyio.run(sender.submit, room("r-1"), "안녕")
    assert window.sent == [("01000000000", "안녕")]


def test_window_sender_refuses_a_room_without_one_mobile_number() -> None:
    window = FakeWindow()
    sender = WindowSender(FakeReader({"r-2": None}), window)
    with pytest.raises(RoomUnverifiedError):
        anyio.run(sender.submit, room("r-2"), "안녕")
    assert window.sent == []
