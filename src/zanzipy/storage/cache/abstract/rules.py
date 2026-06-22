"""Abstract cache interfaces for compiled Zanzibar rules.

This module defines a minimal cache interface focused on storing compiled
representations of rules keyed by ``(namespace, name)``. The compiled value
type is intentionally generic to allow engines to cache either the raw
``RewriteRule`` object or a more processed plan.
"""

from abc import ABC, abstractmethod


class CompiledRuleCache[TCompiled](ABC):
    """Cache interface for compiled rules keyed by (namespace, name)."""

    @abstractmethod
    def get(self, namespace: str, name: str) -> TCompiled | None:
        """Return a cached compiled value or ``None``."""

    @abstractmethod
    def set(self, namespace: str, name: str, compiled: TCompiled) -> None:
        """Populate cache for the given key with ``compiled``."""

    @abstractmethod
    def invalidate(self, namespace: str, name: str) -> None:
        """Invalidate cache entry for the specific rule name."""

    @abstractmethod
    def invalidate_namespace(self, namespace: str) -> None:
        """Invalidate all cached entries within ``namespace``."""

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None

    def info(self) -> dict[str, object]:
        return {}
