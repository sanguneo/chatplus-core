# Copyright (c) 2026 chatplus-core contributors
"""The published contract must match the running app (a spec nobody checks is a rumour)."""

from pathlib import Path

from tools.dump_contract import contract_text


def test_published_contract_matches_app() -> None:
    published = (Path(__file__).resolve().parents[1] / "openapi.json").read_text(encoding="utf-8")
    assert published == contract_text()
