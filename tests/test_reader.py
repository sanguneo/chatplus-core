# Copyright (c) 2026 chatplus-core contributors
from pathlib import Path

import pytest

from chatplus_core import reader as reader_module
from chatplus_core.models import LastMessage
from chatplus_core.reader import (
    InvalidLimitError,
    MessageNotFoundError,
    Reader,
    RoomNotFoundError,
)
from chatplus_core.storage import Profile, StorageError, StorageIssue, read_stable
from chatplus_core.values import Record, ValueFormatError, ValueIssue
from tests.fixtures import encode, profile_at
from tests.test_hive import frame


def room_of(reader: Reader, message_id: str) -> str:
    return next(m.room_id for m in reader.snapshot().messages if m.id == message_id)


def test_cached_rooms_with_same_name_keep_distinct_identity(tmp_path: Path) -> None:
    result = Reader(profile_at(tmp_path)).rooms()
    assert result.complete is True
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


def test_rooms_come_latest_activity_first(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    assert [room.id for room in reader.rooms().rooms] == [
        room_of(reader, "m-img"),
        room_of(reader, "m-new"),
    ]


def test_room_kind_follows_recipients(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    kinds = {room.id: room.kind for room in reader.rooms().rooms}
    assert kinds[room_of(reader, "m-new")] == "direct"
    assert kinds[room_of(reader, "m-img")] == "group"


def test_room_previews_its_newest_message(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    previews = {room.id: room.last_message for room in reader.rooms().rooms}
    assert previews[room_of(reader, "m-new")] == LastMessage(text="new", from_me=True)
    assert previews[room_of(reader, "m-img")] == LastMessage(text="", from_me=False)


def test_messages_map_direction_sender_and_kind(tmp_path: Path) -> None:
    by_id = {m.id: m for m in Reader(profile_at(tmp_path)).snapshot().messages}
    assert (by_id["m-new"].from_me, by_id["m-new"].kind, by_id["m-new"].text) == (
        True,
        "text",
        "new",
    )
    assert by_id["m-old"].from_me is False
    assert by_id["m-old"].sender == "sip:one@synthetic.invalid"
    assert by_id["m-old"].sender_id == "sip:one@synthetic.invalid"
    assert (by_id["m-img"].kind, by_id["m-img"].text) == ("media", "")


def test_rich_body_is_not_misrepresented_as_plain_text(tmp_path: Path) -> None:
    rich = next(m for m in Reader(profile_at(tmp_path)).snapshot().messages if m.id == "m-rich")
    assert rich.text == ""


def test_newest_window_is_chronological_not_frame_order(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    result = reader.messages(room_of(reader, "m-new"), limit=1)
    assert [message.text for message in result.messages] == ["new"]
    assert result.has_earlier is True


def test_before_id_pages_backward_without_gaps(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    room = room_of(reader, "m-new")
    first = reader.messages(room, limit=1)
    second = reader.messages(room, limit=1, before=first.messages[0].id)
    assert [m.id for m in second.messages] == ["m-old"]
    assert second.has_earlier is False


@pytest.mark.parametrize("before", ["m-new", "gone"])
def test_before_id_outside_the_room_is_not_found(before: str, tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    with pytest.raises(MessageNotFoundError):
        _ = reader.messages(room_of(reader, "m-img"), before=before)


@pytest.mark.parametrize("limit", [0, 1001])
def test_limit_outside_the_declared_bound_is_refused(limit: int, tmp_path: Path) -> None:
    with pytest.raises(InvalidLimitError):
        _ = Reader(profile_at(tmp_path)).messages("0" * 64, limit=limit)


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


def test_recipients_that_are_not_a_list_are_refused(tmp_path: Path) -> None:
    profile = profile_at(tmp_path)
    folder = Record(
        37,
        {
            0: {"resourceURL": "https://synthetic.invalid/room/a"},
            3: {"Name": "same title", "Recipients": "sip:one@synthetic.invalid"},
        },
    )
    with (tmp_path / "folder.hive").open("ab") as output:
        _ = output.write(frame("folder-0", encode(folder)))
    with pytest.raises(ValueFormatError) as error:
        _ = Reader(profile).snapshot()
    assert error.value.issue is ValueIssue.RECIPIENTS


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
