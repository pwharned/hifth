from __future__ import annotations

import base64
import json
import urllib.request
from typing import Any

from ..domain import CardDraft


class AnkiConnect:
    def __init__(self, url: str = "http://127.0.0.1:8765", timeout: float = 120) -> None:
        self.url = url
        self.timeout = timeout

    def invoke(self, action: str, **params: Any) -> Any:
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"action": action, "version": 6, "params": params}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        if payload.get("error"):
            raise RuntimeError(f"AnkiConnect {action}: {payload['error']}")
        return payload["result"]


class AnkiPublisher:
    """Idempotently publishes drafts using their stable ID."""

    ID_FIELD = "FlashcardsID"

    def __init__(self, client: AnkiConnect) -> None:
        self.client = client

    def publish(self, card: CardDraft) -> int:
        for asset in card.audio:
            self.client.invoke(
                "storeMediaFile",
                filename=asset.filename,
                data=base64.b64encode(asset.path.read_bytes()).decode(),
            )
        query = f'deck:"{card.deck}" {self.ID_FIELD}:{card.id}'
        existing = self.client.invoke("findNotes", query=query)
        fields = dict(card.fields, **{self.ID_FIELD: card.id})
        if existing:
            self.client.invoke("updateNoteFields", note={"id": existing[0], "fields": fields})
            return int(existing[0])
        note_id = self.client.invoke(
            "addNote",
            note={
                "deckName": card.deck,
                "modelName": card.model,
                "fields": fields,
                "options": {"allowDuplicate": False},
                "tags": list(card.tags),
            },
        )
        return int(note_id)
