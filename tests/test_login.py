# Copyright (c) 2026 chatplus-core contributors
from pathlib import Path

from chatplus_core.login import (
    LockPassword,
    apply_env_file,
    load_lock_password,
    read_env_file,
)


def test_lock_password_repr_hides_the_value() -> None:
    assert "s3cret" not in repr(LockPassword("s3cret"))


def test_lock_password_loads_only_when_set() -> None:
    assert load_lock_password({}) is None
    assert load_lock_password({"CHATPLUS_LOCK_PASSWORD": ""}) is None
    assert load_lock_password({"CHATPLUS_LOCK_PASSWORD": "s3cret"}) == LockPassword("s3cret")


def test_read_env_file_handles_comments_quotes_and_blanks(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    lines = [
        "",
        "# a comment",
        "CHATPLUS_PORT=8767",
        'CHATPLUS_LOCK_PASSWORD="s3 cret"',
        "BAD LINE",
        "EMPTY=",
    ]
    _ = env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert read_env_file(env) == {
        "CHATPLUS_PORT": "8767",
        "CHATPLUS_LOCK_PASSWORD": "s3 cret",
        "EMPTY": "",
    }


def test_read_env_file_absent_is_empty(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "nope.env") == {}


def test_apply_env_file_never_overrides_what_is_already_set(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _ = env.write_text("A=from_file\nB=from_file\n", encoding="utf-8")
    target = {"A": "already"}
    apply_env_file(env, target)
    assert target == {"A": "already", "B": "from_file"}
