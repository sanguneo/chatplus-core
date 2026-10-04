# Copyright (c) 2026 chatplus-core contributors
from chatplus_core.winclient import Line, composer_empty, digits, has_number, ready_to_send


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
