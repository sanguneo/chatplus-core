# Copyright (c) 2026 chatplus-core contributors
import pytest

from chatplus_core.macclient import lock_screen, near, reads_as


def test_a_frame_matches_its_box_within_a_point() -> None:
    assert near((320, 604, 48, 48), (320, 604, 48, 48))
    assert near((321, 603, 49, 47), (320, 604, 48, 48))
    assert not near((323, 604, 48, 48), (320, 604, 48, 48))
    assert not near(None, (320, 604, 48, 48))


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["친구\n010-0000-0001"], True),
        (["010 0000 0001"], True),
        (["친구\n010-0000-00012"], False),
        (["010-0000-0001 외 1명"], False),
        (["친구", "02-123-4567"], False),
        ([], False),
    ],
)
def test_a_result_reads_as_the_number_only_digit_for_digit(
    texts: list[str], *, expected: bool
) -> None:
    assert reads_as(texts, "01000000001") is expected


def test_only_the_lock_mode_title_reads_as_the_lock_screen() -> None:
    assert lock_screen(["잠금 모드", "비밀번호를 입력하세요"])
    assert not lock_screen(["잠금", "모드"])
    assert not lock_screen(["받는 사람 선택"])
