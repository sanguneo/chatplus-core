# Copyright (c) 2026 chatplus-core contributors
import base64
import plistlib
from pathlib import Path

import pytest

from chatplus_core.storage import MAC_PREFERENCES, StorageError, StorageIssue, mac_profile
from tests.test_hive import KEY

PROFILE = "0123456789abcdef0123456789abcdef"


def container(tmp_path: Path, preferences: dict[str, object]) -> Path:
    (tmp_path / "Documents/edb" / PROFILE).mkdir(parents=True)
    plist = tmp_path / MAC_PREFERENCES
    plist.parent.mkdir(parents=True)
    _ = plist.write_bytes(plistlib.dumps(preferences, fmt=plistlib.FMT_BINARY))
    return tmp_path


def encoded(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode("ascii")


def test_the_profile_key_comes_from_shared_preferences(tmp_path: Path) -> None:
    root = container(
        tmp_path,
        {
            f"flutter.cipher_key_{PROFILE}_14CA90B2": encoded(KEY),
            "flutter.cipher_key_someone_else_14CA90B2": encoded(bytes(32)),
            "flutter.account_id": "unrelated",
        },
    )
    profile = mac_profile(root)
    assert profile.directory == root / "Documents/edb" / PROFILE
    assert profile.key == KEY


@pytest.mark.parametrize(
    ("preferences", "issue"),
    [
        ({}, StorageIssue.KEYS),
        (
            {
                f"flutter.cipher_key_{PROFILE}_A": encoded(KEY),
                f"flutter.cipher_key_{PROFILE}_B": encoded(KEY),
            },
            StorageIssue.KEYS,
        ),
        ({f"flutter.cipher_key_{PROFILE}_A": encoded(KEY[:16])}, StorageIssue.HIVE_KEY),
        ({f"flutter.cipher_key_{PROFILE}_A": 7}, StorageIssue.HIVE_KEY),
    ],
)
def test_a_missing_ambiguous_or_malformed_key_is_refused(
    tmp_path: Path, preferences: dict[str, object], issue: StorageIssue
) -> None:
    with pytest.raises(StorageError) as caught:
        _ = mac_profile(container(tmp_path, preferences))
    assert caught.value.issue is issue


def test_two_profiles_are_refused(tmp_path: Path) -> None:
    root = container(tmp_path, {f"flutter.cipher_key_{PROFILE}_A": encoded(KEY)})
    (root / "Documents/edb/another").mkdir()
    with pytest.raises(StorageError) as caught:
        _ = mac_profile(root)
    assert caught.value.issue is StorageIssue.PROFILES
