# Copyright (c) 2026 chatplus-core contributors
from pathlib import Path

import pytest

from chatplus_core import reader as reader_module
from chatplus_core.reader import Reader, RoomNotFoundError
from chatplus_core.storage import Profile, StorageError, StorageIssue, read_stable
from chatplus_core.values import ValueFormatError
from tests.fixtures import encode, profile_at
from tests.test_hive import frame


def test_cached_rooms_with_same_name_keep_distinct_identity(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    result = reader.rooms()
    assert result.total == 2
    assert len({room.id for room in result.rooms}) == 2
    assert {room.title for room in result.rooms} == {"same title"}


def test_newest_time_window_is_chronological_not_frame_order(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    room_id = next(
        message.room_id for message in reader.snapshot().messages if message.id == "m-new"
    )
    result = reader.messages(room_id, limit=1)
    assert [message.text for message in result.messages] == ["new"]
    assert result.total == 2
    assert result.truncated is True


def test_rich_body_is_not_misrepresented_as_plain_text(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    snapshot = reader.snapshot()
    assert next(message for message in snapshot.messages if message.id == "m-rich").text is None


def test_missing_room_is_not_empty_success(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    with pytest.raises(RoomNotFoundError):
        _ = reader.messages("0" * 64)


def test_unknown_nonrecord_is_not_silently_dropped(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)
    with (tmp_path / "object.hive").open("ab") as output:
        _ = output.write(frame("unexpected", encode(1)))
    with pytest.raises(ValueFormatError):
        _ = Reader(profile).snapshot()


def test_profile_representation_hides_the_key(tmp_path: Path) -> None:
    profile = Profile(tmp_path, b"synthetic-secret" * 2)
    assert "synthetic-secret" not in repr(profile)


def test_observed_cross_file_change_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = profile_at(tmp_path)
    changed = False

    def change_between_reads(path: Path) -> bytes:
        nonlocal changed
        data = read_stable(path)
        if path.name == "object.hive" and not changed:
            changed = True
            with (tmp_path / "folder.hive").open("ab") as output:
                _ = output.write(frame("version", encode(2)))
        return data

    monkeypatch.setattr(reader_module, "read_stable", change_between_reads)
    with pytest.raises(StorageError) as error:
        _ = Reader(profile).snapshot()
    assert error.value.issue is StorageIssue.CHANGED
