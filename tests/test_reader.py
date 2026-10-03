# Copyright (c) 2026 chatplus-core contributors
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chatplus_core import reader as reader_module
from chatplus_core.cursor import CursorError, decode_cursor, encode_cursor
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


def test_room_ids_are_account_scoped(tmp_path: Path) -> None:
    one = tmp_path / "account-one"
    one.mkdir()
    two = tmp_path / "account-two"
    two.mkdir()
    ids_one = {room.id for room in Reader(profile_at(one)).rooms().rooms}
    ids_two = {room.id for room in Reader(profile_at(two)).rooms().rooms}
    assert ids_one.isdisjoint(ids_two)


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


def test_cursor_round_trip_and_room_binding() -> None:
    when = datetime(2026, 1, 1, tzinfo=UTC)
    token = encode_cursor("room-x", when, "msg-1")
    assert decode_cursor(token, "room-x") == (when, "msg-1")
    with pytest.raises(CursorError):
        _ = decode_cursor(token, "room-y")


def test_cursor_pagination_pages_backward_without_gaps(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    room_id = next(m.room_id for m in reader.snapshot().messages if m.id == "m-new")
    first = reader.messages(room_id, limit=1)
    assert [m.id for m in first.messages] == ["m-new"]
    assert first.next_cursor is not None
    assert first.truncated is True
    second = reader.messages(room_id, limit=1, cursor=first.next_cursor)
    assert [m.id for m in second.messages] == ["m-old"]
    assert second.next_cursor is None
    assert second.truncated is False


def test_cursor_from_another_room_is_rejected(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    snap = reader.snapshot()
    room_a = next(m.room_id for m in snap.messages if m.id == "m-new")
    room_b = next(m.room_id for m in snap.messages if m.id == "m-rich")
    cursor_a = reader.messages(room_a, limit=1).next_cursor
    assert cursor_a is not None
    with pytest.raises(CursorError):
        _ = reader.messages(room_b, limit=1, cursor=cursor_a)


def test_malformed_cursor_is_rejected(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    room_id = next(m.room_id for m in reader.snapshot().messages if m.id == "m-new")
    with pytest.raises(CursorError):
        _ = reader.messages(room_id, limit=1, cursor="not-a-cursor")


def test_attachment_metadata_without_bytes(tmp_path: Path) -> None:
    by_id = {m.id: m for m in Reader(profile_at(tmp_path)).snapshot().messages}
    image = by_id["m-img"]
    assert image.content_type == "image/jpeg"
    assert image.has_attachment is True
    assert image.text is None
    plain = by_id["m-new"]
    assert plain.content_type is None
    assert plain.has_attachment is False
