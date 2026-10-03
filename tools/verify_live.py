# Copyright (c) 2026 chatplus-core contributors
"""Opt-in local read verification that prints counts only."""

import json
import secrets

from fastapi.testclient import TestClient

from chatplus_core.api import create_app
from chatplus_core.models import Messages, Rooms
from chatplus_core.reader import Reader


def main() -> None:
    """Exercise real decryption through authenticated HTTP without disclosing rows."""
    reader = Reader()
    token = secrets.token_urlsafe(32)
    with TestClient(create_app(token, reader)) as client:
        denied = client.get("/rooms")
        response = client.get("/rooms?limit=1000", headers={"Authorization": f"Bearer {token}"})
        _ = response.raise_for_status()
        rooms = Rooms.model_validate_json(response.content)
        messages = 0
        text_messages = 0
        for room in rooms.rooms:
            response = client.get(
                "/messages",
                params={"roomId": room.id, "limit": 1000},
                headers={"Authorization": f"Bearer {token}"},
            )
            _ = response.raise_for_status()
            payload = Messages.model_validate_json(response.content)
            messages += len(payload.messages)
            text_messages += sum(message.text is not None for message in payload.messages)
        print(
            json.dumps(
                {
                    "unauthenticatedStatus": denied.status_code,
                    "rooms": len(rooms.rooms),
                    "messages": messages,
                    "textMessages": text_messages,
                    "coverage": "local-cache",
                    "sentMessages": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
