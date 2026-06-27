"""In-memory LRU cache for compiled rule values."""

from dataclasses import dataclass

from zanzipy.storage.cache.abstract.rules import CompiledRuleCache
from zanzipy.storage.cache.concrete._lru import LruStore


@dataclass(frozen=True, slots=True)
class _RuleKey:
    namespace: str
    name: str


class LruCompiledRuleCache[TCompiled](CompiledRuleCache[TCompiled]):
    """Thread-safe size-bounded LRU/TTL cache keyed by namespace and rule name."""

    def __init__(
        self, *, max_entries: int = 10000, ttl_seconds: float | None = 300.0
    ) -> None:
        """Configure the in-memory capacity and optional TTL for compiled rules."""
        self._store: LruStore[_RuleKey, TCompiled] = LruStore(
            max_entries=max_entries,
            ttl_seconds=ttl_seconds,
        )

    def get(self, namespace: str, name: str) -> TCompiled | None:
        """Return a compiled rule, or ``None`` on miss or expiry."""
        return self._store.get(_RuleKey(namespace=namespace, name=name))

    def set(self, namespace: str, name: str, compiled: TCompiled) -> None:
        """Cache a compiled rule under its namespace and rule name."""
        self._store.set(_RuleKey(namespace=namespace, name=name), compiled)

    def invalidate(self, namespace: str, name: str) -> None:
        """Remove one compiled rule from the cache if present."""
        self._store.delete(_RuleKey(namespace=namespace, name=name))

    def invalidate_namespace(self, namespace: str) -> None:
        """Remove every compiled rule cached for ``namespace``."""
        self._store.delete_where(lambda key: key.namespace == namespace)

    def ping(self) -> bool:
        """Return ``True`` because the in-memory cache has no dependency."""
        return True

    def close(self) -> None:
        """Clear in-memory entries and release cached rules."""
        self._store.clear()

    def info(self) -> dict[str, object]:
        """Return LRU capacity, TTL, size, and hit/miss diagnostics."""
        return self._store.info()
