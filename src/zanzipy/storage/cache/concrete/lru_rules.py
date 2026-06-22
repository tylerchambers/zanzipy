"""In-memory LRU cache for compiled rules.

Thread-safe, size-bounded LRU with optional TTL, keyed by (namespace, name).
Values are copied on read to avoid aliasing.
"""

from collections import OrderedDict
from dataclasses import dataclass
import threading
import time

from zanzipy.storage.cache.abstract.rules import CompiledRuleCache


@dataclass(frozen=True, slots=True)
class _Key:
    namespace: str
    name: str


@dataclass(slots=True)
class _Entry[TCompiled]:
    value: TCompiled
    expires_at: float | None


class LruCompiledRuleCache[TCompiled](CompiledRuleCache[TCompiled]):
    def __init__(
        self, *, max_entries: int = 10000, ttl_seconds: float | None = 300.0
    ) -> None:
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._lock = threading.RLock()
        self._map: OrderedDict[_Key, _Entry[TCompiled]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def _now(self) -> float:
        return time.monotonic()

    def _expiry(self, now: float) -> float | None:
        return None if self._ttl is None else now + self._ttl

    def _is_expired(self, entry: _Entry[TCompiled], now: float) -> bool:
        return entry.expires_at is not None and now >= entry.expires_at

    def _touch(self, key: _Key) -> None:
        self._map.move_to_end(key, last=True)

    def _evict_if_needed(self) -> None:
        while len(self._map) > self._max_entries:
            self._map.popitem(last=False)

    def get(self, namespace: str, name: str) -> TCompiled | None:
        key = _Key(namespace=namespace, name=name)
        with self._lock:
            entry = self._map.get(key)
            if entry is None:
                self._misses += 1
                return None
            now = self._now()
            if self._is_expired(entry, now):
                self._map.pop(key, None)
                self._misses += 1
                return None
            self._touch(key)
            self._hits += 1
            return entry.value

    def set(self, namespace: str, name: str, compiled: TCompiled) -> None:
        key = _Key(namespace=namespace, name=name)
        with self._lock:
            self._map[key] = _Entry(
                value=compiled, expires_at=self._expiry(self._now())
            )
            self._touch(key)
            self._evict_if_needed()

    def invalidate(self, namespace: str, name: str) -> None:
        key = _Key(namespace=namespace, name=name)
        with self._lock:
            self._map.pop(key, None)

    def invalidate_namespace(self, namespace: str) -> None:
        with self._lock:
            to_delete = [k for k in self._map if k.namespace == namespace]
            for k in to_delete:
                self._map.pop(k, None)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        with self._lock:
            self._map.clear()

    def info(self) -> dict[str, object]:
        with self._lock:
            return {
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl,
                "size": len(self._map),
                "hits": self._hits,
                "misses": self._misses,
            }
