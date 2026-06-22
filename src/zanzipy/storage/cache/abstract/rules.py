"""Abstract cache interface for compiled Zanzibar rules."""

from abc import ABC, abstractmethod


class CompiledRuleCache[TCompiled](ABC):
    """Cache interface for compiled rule values keyed by ``(namespace, name)``."""

    @abstractmethod
    def get(self, namespace: str, name: str) -> TCompiled | None:
        """Return the cached value for ``namespace``/``name`` or ``None``."""

    @abstractmethod
    def set(self, namespace: str, name: str, compiled: TCompiled) -> None:
        """Store ``compiled`` for ``namespace``/``name``."""

    @abstractmethod
    def invalidate(self, namespace: str, name: str) -> None:
        """Remove one cached rule if present."""

    @abstractmethod
    def invalidate_namespace(self, namespace: str) -> None:
        """Remove every cached rule in ``namespace``."""

    def ping(self) -> bool:
        """Return whether the cache is reachable."""

        return True

    def close(self) -> None:
        """Release cache resources."""

        return None

    def info(self) -> dict[str, object]:
        """Return cache diagnostics."""

        return {}
