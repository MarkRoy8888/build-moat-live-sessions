"""Tiny in-memory cache for home detail (Q5).

Real systems use Redis. For the playground, a dict is enough to demonstrate
the hit/miss pattern and ms difference. Toggleable via settings.
"""

from threading import Lock
from time import time


class HomeDetailCache:
    def __init__(self):
        self._store: dict[str, tuple[float, dict]] = {}
        self._lock = Lock()
        self.ttl_seconds = 3600
        self.hits = 0
        self.misses = 0

    def get(self, home_id: str) -> dict | None:
        with self._lock:
            entry = self._store.get(home_id)
            if entry is None:
                self.misses += 1
                return None
            stored_at, value = entry
            if time() - stored_at > self.ttl_seconds:
                del self._store[home_id]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, home_id: str, value: dict) -> None:
        with self._lock:
            self._store[home_id] = (time(), value)

    def invalidate(self, home_id: str) -> None:
        with self._lock:
            self._store.pop(home_id, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._store),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": (self.hits / total) if total else 0.0,
            }


home_cache = HomeDetailCache()
