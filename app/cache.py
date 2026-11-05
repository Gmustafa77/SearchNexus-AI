"""Small bounded TTL cache + query normalisation (no external dependencies)."""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass

_WS = re.compile(r"\s+")


def normalize_query(text: str) -> str:
    """Case/whitespace/trailing-punctuation insensitive key, so 'Who won?' == 'who won'."""
    return _WS.sub(" ", text.casefold().strip()).rstrip("?!. ").strip()


@dataclass
class _Entry[T]:
    value: T
    stored_at: float


class TTLCache[T]:
    """LRU + TTL. ttl_s <= 0 disables the cache entirely."""

    def __init__(self, max_entries: int, ttl_s: float):
        self.max_entries, self.ttl_s = max_entries, ttl_s
        self._data: OrderedDict[str, _Entry[T]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self.ttl_s > 0 and self.max_entries > 0

    def get(self, key: str) -> T | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.stored_at > self.ttl_s:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return entry.value

    def set(self, key: str, value: T) -> None:
        if not self.enabled:
            return
        self._data[key] = _Entry(value, time.monotonic())
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)
