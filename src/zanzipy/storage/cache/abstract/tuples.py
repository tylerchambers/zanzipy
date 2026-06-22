"""Abstract cache interfaces for hot Zanzibar relation tuples.

This module defines a minimal, storage-agnostic cache interface focused on
accelerating hot-path tuple lookups used by the checker/expander engines.

Design goals:
- Keep the surface area small and semantics clear
- Favor forward lookups by object (most common path)
- Optionally support reverse lookups by subject
- Leave filtering to higher layers (e.g., a repository decorator)
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from zanzipy.models import Obj, RelationTuple, Subject


class TupleCache(ABC):
    """Cache interface for relation tuples.

    The cache stores pre-materialized collections of ``RelationTuple`` entries
    keyed by either an object (forward lookups) or a subject (reverse lookups).

    Implementations may apply LRU/TTL strategies and are responsible for their
    own eviction policies.
    """

    @abstractmethod
    def get_by_object(self, obj: Obj) -> Sequence[RelationTuple] | None:
        """Return cached tuples for ``obj`` or ``None`` if not present.

        Implementations should return an immutable or read-only sequence when
        possible to discourage mutation of cached state by callers.
        """

    @abstractmethod
    def set_by_object(self, obj: Obj, tuples: Sequence[RelationTuple]) -> None:
        """Populate the cache entry for ``obj`` with ``tuples``."""

    @abstractmethod
    def invalidate_object(self, obj: Obj) -> None:
        """Invalidate any cached entry associated with ``obj``."""

    @abstractmethod
    def get_by_subject(self, subject: Subject) -> Sequence[RelationTuple] | None:
        """Return cached tuples for ``subject`` or ``None`` if not present."""

    @abstractmethod
    def set_by_subject(self, subject: Subject, tuples: Sequence[RelationTuple]) -> None:
        """Populate the cache entry for ``subject`` with ``tuples``."""

    @abstractmethod
    def invalidate_subject(self, subject: Subject) -> None:
        """Invalidate any cached entry associated with ``subject``."""

    def ping(self) -> bool:
        """Lightweight health check for cache connectivity (optional)."""

        return True

    def close(self) -> None:
        """Release any resources held by the cache (optional)."""

        return None

    def info(self) -> dict[str, object]:
        """Return diagnostic information about the cache (optional)."""

        return {}
