# Copyright (c) 2026 chatplus-core contributors
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chatplus_core.api import create_app
from chatplus_core.icon import ICON_PATH, IconError, IconIssue, load_icon, prepare_icon
from tests.wire import obj, parse

TOKEN = bytes(range(32)).hex()
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SAFE = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1" fill="#fff"/></svg>'


def test_the_bundled_icon_is_a_safe_svg_with_a_pinned_version() -> None:
    icon = load_icon()
    assert icon.media_type == "image/svg+xml"
    assert icon.bytes == ICON_PATH.read_bytes()
    assert len(icon.version) == 16
    assert all(c in "0123456789abcdef" for c in icon.version)
    assert b"#B2CEFF" not in icon.bytes
    assert icon.bytes.count(b"#FFFFFF") == 2


def test_the_version_follows_the_bytes() -> None:
    assert (
        prepare_icon(SAFE, "image/svg+xml").version
        != prepare_icon(SAFE + b" ", "image/svg+xml").version
    )


@pytest.mark.parametrize(
    ("data", "media_type", "issue"),
    [
        pytest.param(b"x" * (64 * 1024 + 1), "image/png", IconIssue.SIZE, id="too-big"),
        pytest.param(SAFE, "image/gif", IconIssue.TYPE, id="wrong-type"),
        pytest.param(b"\xff\xfe x", "image/svg+xml", IconIssue.ENCODING, id="not-utf8"),
        pytest.param(
            b"<svg><script>x</script></svg>", "image/svg+xml", IconIssue.ACTIVE, id="script"
        ),
        pytest.param(b'<svg onload="x"></svg>', "image/svg+xml", IconIssue.ACTIVE, id="handler"),
        pytest.param(b"<svg><!-- c --></svg>", "image/svg+xml", IconIssue.ACTIVE, id="comment"),
        pytest.param(
            b'<svg><use href="http://x/#a"/></svg>', "image/svg+xml", IconIssue.REFERENCE, id="href"
        ),
        pytest.param(
            b'<svg><rect fill="url(http://x)"/></svg>',
            "image/svg+xml",
            IconIssue.REFERENCE,
            id="url",
        ),
    ],
)
def test_unsafe_or_wrong_icons_are_refused(data: bytes, media_type: str, issue: IconIssue) -> None:
    with pytest.raises(IconError) as caught:
        _ = prepare_icon(data, media_type)
    assert caught.value.issue is issue


def test_a_same_document_reference_is_allowed() -> None:
    icon = prepare_icon(b'<svg><use href="#a"/><rect fill="url(#a)"/></svg>', "image/svg+xml")
    assert icon.media_type == "image/svg+xml"


def test_icon_route_serves_the_bytes_inert_and_capabilities_names_the_version() -> None:
    icon = load_icon()
    with TestClient(create_app(TOKEN)) as client:
        provider = obj(obj(parse(client.get("/capabilities", headers=AUTH).content))["provider"])
        answer = client.get("/icon", headers=AUTH)
    assert provider["icon"] == icon.version
    assert answer.status_code == 200
    assert answer.content == icon.bytes
    assert answer.headers["content-type"].startswith("image/svg+xml")
    assert answer.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert answer.headers["x-content-type-options"] == "nosniff"


def test_a_bad_bundled_icon_stops_startup(tmp_path: Path) -> None:
    bad = tmp_path / "icon.svg"
    _ = bad.write_bytes(b"<svg><script/></svg>")
    with pytest.raises(IconError):
        _ = load_icon(bad)


def test_the_icon_route_needs_the_token() -> None:
    with TestClient(create_app(TOKEN)) as client:
        assert client.get("/icon").status_code == 401
