# Copyright (c) 2026 chatplus-core contributors
"""The adapter's own provider icon: a static image, checked once and served inert.

Mirrors tsuba serve-kit's icon rules so saya draws it like any other provider's.
An SVG that could run or reach outside itself is refused at start, not served.
"""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

MAX_ICON_BYTES: Final = 64 * 1024
ICON_TYPES: Final = ("image/svg+xml", "image/png", "image/webp")
# Active content, and the indirection that can hide it: entities, CSS escapes,
# comments, external or animatable references.
UNSAFE: Final = (
    re.compile(r"<!|<\?|&|\\|/\*"),
    re.compile(r"<(?:[\w.-]+:)?(?:script|foreignObject|animate\w*|set|discard)\b", re.IGNORECASE),
    re.compile(r"\s(?:[\w.-]+:)?on[\w.-]*\s*=", re.IGNORECASE),
    re.compile(r"\sxml:base\s*=", re.IGNORECASE),
    re.compile(r"@import\b", re.IGNORECASE),
)
HREF: Final = re.compile(r"\s(?:[\w.-]+:)?href\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)
FRAGMENT: Final = re.compile(r"^#\S+$")
LOCAL_URL: Final = re.compile(r"\burl\s*\(\s*([\"']?)#[^\s\"'()]+\1\s*\)", re.IGNORECASE)
ANY_URL: Final = re.compile(r"\burl\s*\(", re.IGNORECASE)


class IconIssue(StrEnum):
    """Why a provider icon cannot be served."""

    SIZE = "Provider icon exceeds 64 KiB"
    TYPE = "Unsupported provider icon type"
    ENCODING = "Provider SVG is not valid UTF-8"
    ACTIVE = "Provider SVG has active content"
    REFERENCE = "Provider SVG references must be same-document fragments"


class IconError(Exception):
    """A provider icon that fails the static safety rules; the adapter does not start."""

    def __init__(self, issue: IconIssue) -> None:
        """Keep a stable reason without the icon's bytes."""
        self.issue: IconIssue = issue
        super().__init__(str(issue))


@dataclass(frozen=True, slots=True)
class Icon:
    """A checked icon ready to serve, with a version that changes with its bytes."""

    bytes: bytes
    media_type: str
    version: str


def _check_svg(data: bytes) -> None:
    try:
        svg = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IconError(IconIssue.ENCODING) from exc
    if any(pattern.search(svg) for pattern in UNSAFE):
        raise IconError(IconIssue.ACTIVE)
    for match in HREF.finditer(svg):
        if not FRAGMENT.match(match.group(2)):
            raise IconError(IconIssue.REFERENCE)
    if ANY_URL.search(LOCAL_URL.sub("", svg)):
        raise IconError(IconIssue.REFERENCE)


def prepare_icon(data: bytes, media_type: str) -> Icon:
    """Check an adapter-owned icon and pin its version, or refuse to serve it."""
    if len(data) > MAX_ICON_BYTES:
        raise IconError(IconIssue.SIZE)
    if media_type not in ICON_TYPES:
        raise IconError(IconIssue.TYPE)
    if media_type == "image/svg+xml":
        _check_svg(data)
    return Icon(data, media_type, hashlib.sha256(data).hexdigest()[:16])


ICON_PATH: Final = Path(__file__).with_name("icon.svg")


def load_icon(path: Path | None = None) -> Icon:
    """Read and check the bundled provider icon."""
    return prepare_icon((path or ICON_PATH).read_bytes(), "image/svg+xml")
