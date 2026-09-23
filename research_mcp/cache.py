"""Bounded cache keyed by semantic-index generation and model version."""

from __future__ import annotations

import json
from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
from typing import Any


class SemanticCache:
    def __init__(self, max_entries: int = 256) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._entries: OrderedDict[str, Any] = OrderedDict()

    @staticmethod
    def _key(query: str, filters: dict, model: str, generation: int) -> str:
        payload = json.dumps(
            [" ".join(query.split()).casefold(), filters, model, generation],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return sha256(payload.encode()).hexdigest()

    def get(self, query: str, filters: dict, model: str, generation: int):
        key = self._key(query, filters, model, generation)
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return deepcopy(self._entries[key])

    def put(self, query: str, filters: dict, model: str, generation: int, value) -> None:
        key = self._key(query, filters, model, generation)
        self._entries[key] = deepcopy(value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

