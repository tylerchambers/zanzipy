"""Abstract cache interfaces for tenant-scoped Zanzibar relation tuple buckets."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from zanzipy.models import Obj, RelationTuple, Subject
    from zanzipy.storage.revision import ReadContext


class TupleCache(ABC):
    """Cache interface for immutable tenant and revision scoped tuple buckets."""

    @abstractmethod
    def get_by_object(
        self,
        obj: Obj,
        *,
        context: ReadContext,
    ) -> Sequence[RelationTuple] | None:
        """Return cached tuples for ``obj`` in ``context`` or ``None``."""

    @abstractmethod
    def set_by_object(
        self,
        obj: Obj,
        *,
        context: ReadContext,
        tuples: Sequence[RelationTuple],
    ) -> None:
        """Populate the cache entry for ``obj`` in ``context``."""

    @abstractmethod
    def get_by_subject(
        self,
        subject: Subject,
        *,
        context: ReadContext,
    ) -> Sequence[RelationTuple] | None:
        """Return cached tuples for ``subject`` in ``context`` or ``None``."""

    @abstractmethod
    def set_by_subject(
        self,
        subject: Subject,
        *,
        context: ReadContext,
        tuples: Sequence[RelationTuple],
    ) -> None:
        """Populate the cache entry for ``subject`` in ``context``."""

    def ping(self) -> bool:
        """Return whether the cache is reachable."""

        return True

    def close(self) -> None:
        """Release cache resources."""

        return None

    def info(self) -> dict[str, object]:
        """Return cache diagnostics."""

        return {}
