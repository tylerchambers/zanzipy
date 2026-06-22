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
        self._store: LruStore[_RuleKey, TCompiled] = LruStore(
            max_entries=max_entries,
            ttl_seconds=ttl_seconds,
        )

    def get(self, namespace: str, name: str) -> TCompiled | None:
        return self._store.get(_RuleKey(namespace=namespace, name=name))

    def set(self, namespace: str, name: str, compiled: TCompiled) -> None:
        self._store.set(_RuleKey(namespace=namespace, name=name), compiled)

    def invalidate(self, namespace: str, name: str) -> None:
        self._store.delete(_RuleKey(namespace=namespace, name=name))

    def invalidate_namespace(self, namespace: str) -> None:
        self._store.delete_where(lambda key: key.namespace == namespace)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        self._store.clear()

    def info(self) -> dict[str, object]:
        return self._store.info()
