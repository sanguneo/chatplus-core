# Copyright (c) 2026 chatplus-core contributors
"""Opt-in local read verification over the tsuba routes that prints counts only."""

import json
import secrets
from collections import Counter

from fastapi.testclient import TestClient

from chatplus_core.api import create_app
from chatplus_core.models import MessagesAnswer, RoomsAnswer
from chatplus_core.reader import MAX_LIMIT, Reader

PAGE = 5


def read(client: TestClient, auth: dict[str, str], params: dict[str, str]) -> MessagesAnswer:
    """Read one window of a room's messages."""
    response = client.get("/messages", params=params, headers=auth)
    _ = response.raise_for_status()
    return MessagesAnswer.model_validate_json(response.content)


def paged_ids(client: TestClient, auth: dict[str, str], room_id: str) -> list[str]:
    """Walk a room backward with `before` and return every id, oldest first."""
    ids: list[str] = []
    before: str | None = None
    while True:
        params = {"roomId": room_id, "limit": str(PAGE)}
        if before is not None:
            params["before"] = before
        page = read(client, auth, params)
        ids[:0] = [message.id for message in page.messages]
        if not page.has_earlier or not page.messages:
            return ids
        before = page.messages[0].id


def main() -> None:
    """Exercise real decryption through authenticated HTTP without disclosing rows."""
    token = secrets.token_urlsafe(32)
    auth = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(token, Reader())) as client:
        denied = client.get("/rooms")
        response = client.get("/rooms", headers=auth)
        _ = response.raise_for_status()
        rooms = RoomsAnswer.model_validate_json(response.content)
        counts: Counter[str] = Counter()
        histories: dict[str, list[str]] = {}
        for room in rooms.rooms:
            counts[f"room:{room.kind}"] += 1
            window = read(client, auth, {"roomId": room.id, "limit": str(MAX_LIMIT)})
            histories[room.id] = [message.id for message in window.messages]
            for message in window.messages:
                counts["messages"] += 1
                counts[f"kind:{message.kind}"] += 1
                counts["withText"] += message.text != ""
                counts["fromMe"] += message.from_me
        largest = max(histories, key=lambda room_id: len(histories[room_id]))
        print(
            json.dumps(
                {
                    "health": client.get("/health").status_code,
                    "unauthenticatedStatus": denied.status_code,
                    "rooms": len(rooms.rooms),
                    "complete": rooms.complete,
                    **dict(sorted(counts.items())),
                    "largestRoomMessages": len(histories[largest]),
                    "beforePagingMatchesHistory": paged_ids(client, auth, largest)
                    == histories[largest],
                    "sentMessages": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
