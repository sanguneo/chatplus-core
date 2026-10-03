# Copyright (c) 2026 chatplus-core contributors
"""Typed access to JSON answers and SSE frames, so tests never handle untyped values."""

from pydantic import JsonValue, TypeAdapter

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def parse(raw: bytes | str) -> JsonValue:
    return _JSON.validate_json(raw)


def obj(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def arr(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def string(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def integer(value: JsonValue) -> int:
    assert isinstance(value, int)
    assert not isinstance(value, bool)
    return value


def error_code(raw: bytes) -> str:
    body = obj(parse(raw))
    assert body["ok"] is False
    return string(obj(body["error"])["code"])


def sse(frame: str) -> tuple[str, JsonValue]:
    event, data = frame.removesuffix("\n\n").split("\n")
    return event.removeprefix("event: "), parse(data.removeprefix("data: "))
