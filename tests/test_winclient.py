# Copyright (c) 2026 chatplus-core contributors
from typing import final

import anyio
import pytest

from chatplus_core.envelope import ErrorCode
from chatplus_core.models import Room
from chatplus_core.sender import RoomUnverifiedError
from chatplus_core.winclient import (
    LOCK_MODE,
    LOGGED_OUT,
    ClientAppLockedError,
    Line,
    WindowSender,
    composer_empty,
    digits,
    fits,
    has_number,
    password_screen,
    ready_to_send,
    underlined,
)


def test_a_number_matches_only_digit_for_digit() -> None:
    lines = [Line(10, "친구"), Line(30, "010-0000-0001")]
    found = has_number(lines, "01000000001")
    assert found is not None
    assert found.top == 30
    assert has_number(lines, "0100000000") is None
    assert has_number(lines, "01000000010") is None
    assert digits("010 0000-0001") == "01000000001"


def test_only_the_lock_mode_title_reads_as_the_lock_screen() -> None:
    title = [Line(93, "010-0**0-0**0 님,"), Line(123, "잠금 모드로 로그인되었습니다.")]
    assert password_screen(title) is LOCK_MODE
    assert password_screen([Line(123, "잠금모드로 로그인되었습니다.")]) is LOCK_MODE
    assert password_screen([Line(57, "채팅"), Line(121, "개인"), Line(166, "잠금")]) is None
    assert password_screen([]) is None


def test_the_automatic_logout_asks_for_the_same_password_in_its_own_field() -> None:
    # The idle timeout shows this screen, not lock mode (seen 2026-10-06).
    assert password_screen([Line(88, "그아웃되었습니다. 비밀번호를 입력해")]) is LOGGED_OUT
    assert password_screen([Line(88, "보안을 위해 자동 로그아웃 되었습니다.")]) is LOGGED_OUT
    assert password_screen([Line(57, "로그아웃")]) is None
    assert LOGGED_OUT.underline is not None


def test_only_a_blue_underline_reads_as_a_focused_field() -> None:
    grey = bytes((200, 200, 200, 255)) * 2304
    blue = bytes((235, 120, 40, 255)) * 576 + grey[: 4 * (2304 - 576)]
    assert not underlined(grey)
    assert underlined(blue)


def test_a_scaled_window_a_few_pixels_taller_still_fits() -> None:
    # Measured 2026-10-06: the client's minimum height rounds past the driven one
    # at every scale but 100%, by 2 px at 125% and 5 px at 150%.
    assert fits((768, 679), (768, 679), 1.0)
    assert fits((960, 851), (960, 849), 1.25)
    assert fits((1152, 1023), (1152, 1018), 1.5)
    assert not fits((1344, 1040), (1344, 1188), 1.75)
    assert not fits((960, 849), (900, 849), 1.25)


def test_an_unopened_lock_is_reauth_required() -> None:
    assert ClientAppLockedError().code is ErrorCode.REAUTH_REQUIRED


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
