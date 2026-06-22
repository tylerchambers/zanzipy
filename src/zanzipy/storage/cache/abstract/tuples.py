"""Abstract cache interfaces for hot Zanzibar relation tuples.

The tuple cache stores broad object and subject buckets used by repository
read-through decorators. Filtering stays in the repository layer: a cached bucket
may contain more tuples than a specific request needs, and callers must reapply
``TupleFilter`` before returning cached data.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from zanzipy.models import Obj, RelationTuple, Subject


class TupleCache(ABC):
    """Cache interface for relation tuple buckets.

    ``get_by_object``/``set_by_object`` use exact object identity. Subject keys
    include ``Subject.relation`` when present; a subject with ``relation is None``
    represents the broad reverse bucket for that namespace/id, not an
    exact-direct-only lookup.
    """

    @abstractmethod
    def get_by_object(self, obj: Obj) -> Sequence[RelationTuple] | None:
        """Return cached tuples for ``obj`` or ``None`` if not present."""

    @abstractmethod
    def set_by_object(self, obj: Obj, tuples: Sequence[RelationTuple]) -> None:
        """Populate the cache entry for ``obj`` with ``tuples``."""

    @abstractmethod
    def invalidate_object(self, obj: Obj) -> None:
        """Invalidate the cached object bucket for ``obj``."""

    @abstractmethod
    def get_by_subject(self, subject: Subject) -> Sequence[RelationTuple] | None:
        """Return cached tuples for ``subject`` or ``None`` if not present."""

    @abstractmethod
    def set_by_subject(self, subject: Subject, tuples: Sequence[RelationTuple]) -> None:
        """Populate the cache entry for ``subject`` with ``tuples``."""

    @abstractmethod
    def invalidate_subject(self, subject: Subject) -> None:
        """Invalidate the cached subject bucket for ``subject``."""

    def ping(self) -> bool:
        """Return whether the cache is reachable."""

        return True

    def close(self) -> None:
        """Release cache resources."""

        return None

    def info(self) -> dict[str, object]:
        """Return cache diagnostics."""

        return {}
