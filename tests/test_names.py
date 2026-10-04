# Copyright (c) 2026 chatplus-core contributors
from pathlib import Path

import pytest

from chatplus_core.reader import (
    Reader,
    address_label,
    contact_names,
    format_number,
    national_number,
)
from chatplus_core.storage import Profile
from chatplus_core.values import Record
from tests.fixtures import encode
from tests.test_hive import KEY, frame

FRIEND = "tel:+821000000001"
STRANGER = "tel:+821000000002"
ME = "tel:+821000000009"


@pytest.mark.parametrize(
    ("digits", "shown"),
    [
        ("01000000001", "010-0000-0001"),
        ("0100000001", "010-000-0001"),
        ("0200000001", "02-0000-0001"),
        ("020000001", "02-000-0001"),
        ("0310000001", "031-000-0001"),
        ("15880000", "1588-0000"),
        ("114", "114"),
    ],
)
def test_numbers_are_grouped_as_a_korean_phone_shows_them(digits: str, shown: str) -> None:
    assert format_number(digits) == shown


@pytest.mark.parametrize(
    ("uri", "national"),
    [
        (FRIEND, "01000000001"),
        ("tel:010-0000-0001", "01000000001"),
        ("tel:+82 10 0000 0001;phone-context=x", "01000000001"),
        ("tel:114", "114"),
        ("tel:+15550100", None),
        ("sip:one@synthetic.invalid", None),
        ("tel:", None),
    ],
)
def test_national_numbers_come_only_from_korean_tel_uris(uri: str, national: str | None) -> None:
    assert national_number(uri) == national


def test_addresses_show_a_name_or_the_number_never_the_scheme() -> None:
    names = {"01000000001": "가상 친구"}
    assert address_label(FRIEND, names) == "가상 친구"
    assert address_label(STRANGER, names) == "010-0000-0002"
    assert address_label("tel:+15550100", names) == "+15550100"
    assert address_label("sip:one@synthetic.invalid", names) == "sip:one@synthetic.invalid"


def contact(contact_id: str, number: str, name: str) -> Record:
    return Record(
        133,
        {0: contact_id, 1: [number], 2: name, 4: False, 5: "", 7: f"tel:{number}"},
    )


def contact_box(directory: Path, contacts: list[Record]) -> bytes:
    bookkeeping = frame("version", encode(1)) + frame("last_send_time", encode(1700000000000))
    bookkeeping += frame("did_sync_starred", encode(value=True))
    blob = bookkeeping + b"".join(frame(str(item.fields[0]), encode(item)) for item in contacts)
    _ = (directory / "contact_list.hive").write_bytes(blob)
    return blob


def test_contacts_name_numbers_and_skip_bookkeeping(tmp_path: Path) -> None:
    blob = contact_box(
        tmp_path,
        [
            contact("1", "01000000001", " 가상 친구 "),
            contact("2", "+821000000003", "가상 동료"),
            contact("3", "01000000004", "가상 이름 하나"),
            contact("4", "01000000004", "가상 이름 둘"),
        ],
    )
    names = contact_names(blob, Profile(tmp_path, KEY))
    assert names == {"01000000001": "가상 친구", "01000000003": "가상 동료"}


def folder(url: str, address: str) -> Record:
    return Record(
        37,
        {
            0: {"resourceURL": url},
            3: {"Name": address, "Date": "2026-01-01T02:00:00Z", "Recipients": [address]},
        },
    )


def message(url: str, message_id: str, address: str, direction: str) -> Record:
    return Record(
        38,
        {
            0: {"parentFolder": url},
            1: message_id,
            6: {
                "Date": "2026-01-01T01:00:00Z",
                "TextContent": "가상 본문",
                "Direction": direction,
                "From": address,
            },
        },
    )


def tel_profile(directory: Path) -> Profile:
    friend, stranger = "https://synthetic.invalid/room/f", "https://synthetic.invalid/room/s"
    _ = (directory / "folder.hive").write_bytes(
        frame("version", encode(1))
        + frame("folder-0", encode(folder(friend, FRIEND)))
        + frame("folder-1", encode(folder(stranger, STRANGER)))
    )
    messages = [
        message(friend, "m-in", FRIEND, "In"),
        message(friend, "m-out", ME, "Out"),
        message(stranger, "m-other", STRANGER, "In"),
    ]
    _ = (directory / "object.hive").write_bytes(
        frame("version", encode(1))
        + b"".join(frame(str(item.fields[1]), encode(item)) for item in messages)
    )
    return Profile(directory, KEY)


def test_rooms_and_senders_carry_contact_names_and_numbers_without_tel(tmp_path: Path) -> None:
    profile = tel_profile(tmp_path)
    _ = contact_box(
        tmp_path,
        [contact("1", "01000000001", "가상 친구"), contact("9", "01000000009", "가상 나")],
    )
    snapshot = Reader(profile).snapshot()
    assert sorted(room.title for room in snapshot.rooms) == ["010-0000-0002", "가상 친구"]
    senders = {item.id: (item.sender, item.sender_id) for item in snapshot.messages}
    assert senders == {
        "m-in": ("가상 친구", FRIEND),
        "m-out": ("가상 나", ME),
        "m-other": ("010-0000-0002", STRANGER),
    }


@pytest.mark.parametrize("contacts", [None, b"not a hive box"])
def test_a_missing_or_unreadable_contact_box_leaves_numbers(
    tmp_path: Path, contacts: bytes | None
) -> None:
    profile = tel_profile(tmp_path)
    if contacts is not None:
        _ = (tmp_path / "contact_list.hive").write_bytes(contacts)
    snapshot = Reader(profile).snapshot()
    assert sorted(room.title for room in snapshot.rooms) == ["010-0000-0001", "010-0000-0002"]
    assert all(not item.sender.startswith("tel:") for item in snapshot.messages)
