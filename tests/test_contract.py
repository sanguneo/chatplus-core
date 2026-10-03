# Copyright (c) 2026 chatplus-core contributors
"""Conformance with the pinned tsuba contract: version, codes, routes, features, shapes.

`contract/tsuba.openapi.json` is a byte-for-byte copy of tsuba's contract, the
same bytes saya pins. A specification nobody checks is a rumour, so every
answer and event this adapter gives is checked against it here.
"""

import hashlib
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue

from chatplus_core.api import CONTRACT_VERSION, create_app, stream_events
from chatplus_core.envelope import STATUS, ErrorCode
from chatplus_core.reader import Reader
from chatplus_core.stream import initial_positions
from tests.fixtures import profile_at
from tests.wire import arr, error_code, integer, obj, parse, sse, string

CONTRACT = Path(__file__).resolve().parents[1] / "contract" / "tsuba.openapi.json"
PINNED_SHA256 = "420cbb7fb731b6c6cd924aef7ccd8965361d5099cce4c29c6aed8a9d31a69968"
DOCUMENT = obj(parse(CONTRACT.read_bytes()))
TOKEN = bytes(range(32)).hex()
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PROBES: dict[str, dict[str, str]] = {"/stream": {"since": ":"}}
OPERATIONS = [
    (method.upper(), path, obj(operation))
    for path, item in obj(DOCUMENT["paths"]).items()
    for method, operation in obj(item).items()
]


def resolve(node: JsonValue) -> dict[str, JsonValue]:
    body = obj(node)
    reference = body.get("$ref")
    if reference is None:
        return body
    target: JsonValue = DOCUMENT
    for part in string(reference).removeprefix("#/").split("/"):
        target = obj(target)[part]
    return resolve(target)


def schema(name: str) -> dict[str, JsonValue]:
    return resolve(obj(obj(DOCUMENT["components"])["schemas"])[name])


def conforms_string(spec: dict[str, JsonValue], value: JsonValue) -> None:
    text = string(value)
    minimum = integer(spec["minLength"]) if "minLength" in spec else 0
    assert len(text) >= minimum
    if "pattern" in spec:
        assert re.search(string(spec["pattern"]), text)
    if spec.get("format") == "date-time":
        assert datetime.fromisoformat(text).tzinfo is not None


def conforms_object(spec: dict[str, JsonValue], value: dict[str, JsonValue]) -> None:
    properties: dict[str, JsonValue] = obj(spec["properties"]) if "properties" in spec else {}
    required: list[JsonValue] = arr(spec["required"]) if "required" in spec else []
    extra = spec.get("additionalProperties")
    for key in required:
        assert string(key) in value
    for key, item in value.items():
        if key in properties:
            conforms(properties[key], item)
        else:
            assert extra is not None, f"undocumented key {key}"
            conforms(extra, item)


def conforms(node: JsonValue, value: JsonValue) -> None:
    """Check a value against the subset of JSON Schema the contract uses; null never passes."""
    spec = resolve(node)
    assert value is not None
    if "const" in spec:
        assert value == spec["const"]
    if "enum" in spec:
        assert value in arr(spec["enum"])
    kind = spec.get("type")
    if kind == "string":
        conforms_string(spec, value)
    elif kind == "integer":
        minimum = integer(spec["minimum"]) if "minimum" in spec else 0
        assert integer(value) >= minimum
    elif kind == "boolean":
        assert isinstance(value, bool)
    elif kind == "array":
        for item in arr(value):
            conforms(spec["items"], item)
    if kind == "object" or "properties" in spec or "required" in spec:
        conforms_object(spec, obj(value))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(TOKEN, Reader(profile_at(tmp_path)))) as test_client:
        yield test_client


def capabilities(client: TestClient) -> dict[str, JsonValue]:
    return obj(parse(client.get("/capabilities", headers=AUTH).content))


def test_the_pinned_contract_is_unchanged() -> None:
    assert hashlib.sha256(CONTRACT.read_bytes()).hexdigest() == PINNED_SHA256


def test_capabilities_answer_the_pinned_version(client: TestClient) -> None:
    assert obj(DOCUMENT["info"])["version"] == CONTRACT_VERSION
    assert obj(capabilities(client)["contract"])["version"] == CONTRACT_VERSION


def test_error_codes_and_statuses_match_the_contract() -> None:
    error_schema = schema("ErrorCode")
    documented: dict[str, int] = {}
    for line in string(error_schema["description"]).splitlines():
        if line.startswith("- `"):
            code, _, rest = line.removeprefix("- `").partition("` ")
            documented[code] = int(rest[:3])
    assert documented == {code.value: status for code, status in STATUS.items()}
    enum = {string(code) for code in arr(error_schema["enum"])}
    assert enum == {code.value for code in ErrorCode}


@pytest.mark.parametrize(
    ("method", "path"),
    [(method, path) for method, path, _ in OPERATIONS],
    ids=[f"{method} {path}" for method, path, _ in OPERATIONS],
)
def test_every_route_but_health_requires_the_token(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path)
    if path == "/health":
        assert response.status_code == 200
    else:
        assert response.status_code == 401
        assert error_code(response.content) == "UNAUTHORIZED"


@pytest.mark.parametrize(
    ("method", "path", "operation"),
    OPERATIONS,
    ids=[f"{method} {path}" for method, path, _ in OPERATIONS],
)
def test_every_documented_route_is_routed_by_its_feature(
    client: TestClient, method: str, path: str, operation: dict[str, JsonValue]
) -> None:
    feature = operation.get("x-feature")
    response = client.request(method, path, params=PROBES.get(path), headers=AUTH)
    if feature is None:
        has_icon = "icon" in obj(capabilities(client)["provider"])
        assert response.status_code == (404 if path == "/icon" and not has_icon else 200)
    elif string(feature) in obj(capabilities(client)["features"]):
        assert response.status_code not in {404, 405, 501}
    else:
        assert response.status_code == 501
        assert error_code(response.content) == "NOT_SUPPORTED"


def test_declared_features_conform(client: TestClient) -> None:
    answer = capabilities(client)
    conforms(schema("Capabilities"), answer)
    documented = obj(schema("Features")["properties"])
    declared = obj(answer["features"])
    for feature in declared:
        spec = resolve(documented[feature])
        assert spec.get("x-reserved") is not True
        requires: list[JsonValue] = arr(spec["x-requires"]) if "x-requires" in spec else []
        assert all(string(other) in declared for other in requires)
    assert capabilities(client)["revision"] == answer["revision"]


def test_rooms_messages_and_health_conform(client: TestClient) -> None:
    conforms(schema("Health"), parse(client.get("/health").content))
    rooms = parse(client.get("/rooms", headers=AUTH).content)
    conforms(schema("RoomsAnswer"), rooms)
    for room in arr(obj(rooms)["rooms"]):
        params = {"roomId": string(obj(room)["id"])}
        answer = parse(client.get("/messages", params=params, headers=AUTH).content)
        conforms(schema("MessagesAnswer"), answer)


def test_error_answers_conform_with_their_status(client: TestClient) -> None:
    for response in (
        client.get("/rooms"),
        client.get("/messages", headers=AUTH),
        client.get("/messages", params={"roomId": "0" * 64}, headers=AUTH),
        client.post("/send", headers=AUTH),
        client.get("/send", headers=AUTH),
    ):
        conforms(schema("ErrorEnvelope"), parse(response.content))
        assert response.status_code == STATUS[ErrorCode(error_code(response.content))]


def test_stream_events_conform(tmp_path: Path) -> None:
    reader = Reader(profile_at(tmp_path))
    snapshot = reader.snapshot()
    room = next(m.room_id for m in snapshot.messages if m.id == "m-new")
    stream = obj(obj(obj(DOCUMENT["paths"])["/stream"])["get"])
    events = obj(stream["x-sse-events"])

    async def pull() -> list[str]:
        positions = initial_positions(snapshot, {room: "m-old"})
        frames = stream_events(reader, snapshot, positions, resumed=True, poll_seconds=0)
        try:
            return [await anext(frames) for _ in range(3)]
        finally:
            await frames.aclose()

    frames = [sse(frame) for frame in anyio.run(pull)]
    assert [name for name, _ in frames] == ["ready", "message", "cursor"]
    for name, data in frames:
        conforms(obj(events[name])["schema"], data)
