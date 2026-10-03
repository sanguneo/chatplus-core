# Copyright (c) 2026 chatplus-core contributors
"""Regenerate the published API contract (openapi.json) deterministically.

saya pins this file byte for byte, so it is generated with stable key ordering
and committed; tests/test_contract.py fails if the running app drifts from it.
"""

import json
from pathlib import Path

from chatplus_core.api import create_app

PLACEHOLDER_TOKEN = "x" * 32


def contract_text() -> str:
    """Serialize the app's OpenAPI document with stable, diff-friendly ordering."""
    document = create_app(PLACEHOLDER_TOKEN).openapi()
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    """Write the contract to the repository root."""
    target = Path(__file__).resolve().parents[1] / "openapi.json"
    _ = target.write_text(contract_text(), encoding="utf-8")


if __name__ == "__main__":
    main()
