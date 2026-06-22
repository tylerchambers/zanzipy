"""Small thread-safe LRU/TTL store used by cache implementations."""

from collections import OrderedDict
from dataclasses import dataclass
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(slots=True)
class _Entry[TValue]:
    value: TValue
    expires_at: float | None


class LruStore[TKey, TValue]:
    """Bounded LRU map with optional monotonic-clock TTL."""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float | None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_entries < 0:
            raise ValueError("max_entries must be non-negative")
        if ttl_seconds is not None and ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative or None")

        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = time.monotonic if clock is None else clock
        self._lock = threading.RLock()
        self._entries: OrderedDict[TKey, _Entry[TValue]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: TKey) -> TValue | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if self._is_expired(entry):
                self._entries.pop(key, None)
                self._misses += 1
                return None
            self._entries.move_to_end(key, last=True)
            self._hits += 1
            return entry.value

    def set(self, key: TKey, value: TValue) -> None:
        with self._lock:
            self._entries[key] = _Entry(value=value, expires_at=self._expires_at())
            self._entries.move_to_end(key, last=True)
            self._evict_if_needed()

    def delete(self, key: TKey) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def delete_where(self, predicate: Callable[[TKey], bool]) -> None:
        with self._lock:
            keys = [key for key in self._entries if predicate(key)]
            for key in keys:
                self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def count_where(self, predicate: Callable[[TKey], bool]) -> int:
        with self._lock:
            return sum(1 for key in self._entries if predicate(key))

    def info(self) -> dict[str, object]:
        with self._lock:
            return {
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl_seconds,
                "size": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
            }

    def _expires_at(self) -> float | None:
        if self._ttl_seconds is None:
            return None
        return self._clock() + self._ttl_seconds

    def _is_expired(self, entry: _Entry[TValue]) -> bool:
        return entry.expires_at is not None and self._clock() >= entry.expires_at

    def _evict_if_needed(self) -> None:
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
