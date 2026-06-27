"""Abstract cache interfaces for revisioned Zanzibar relation tuple buckets."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from zanzipy.models import Obj, RelationTuple, Subject
    from zanzipy.storage.revision import Revision


class TupleCache(ABC):
    """Cache interface for immutable revision-scoped relation tuple buckets."""

    @abstractmethod
    def get_by_object(
        self,
        obj: Obj,
        *,
        revision: Revision,
    ) -> Sequence[RelationTuple] | None:
        """Return cached tuples for ``obj`` at ``revision`` or ``None``."""

    @abstractmethod
    def set_by_object(
        self,
        obj: Obj,
        *,
        revision: Revision,
        tuples: Sequence[RelationTuple],
    ) -> None:
        """Populate the cache entry for ``obj`` at ``revision``."""

    @abstractmethod
    def get_by_subject(
        self,
        subject: Subject,
        *,
        revision: Revision,
    ) -> Sequence[RelationTuple] | None:
        """Return cached tuples for ``subject`` at ``revision`` or ``None``."""

    @abstractmethod
    def set_by_subject(
        self,
        subject: Subject,
        *,
        revision: Revision,
        tuples: Sequence[RelationTuple],
    ) -> None:
        """Populate the cache entry for ``subject`` at ``revision``."""

    def ping(self) -> bool:
        """Return whether the cache is reachable."""

        return True

    def close(self) -> None:
        """Release cache resources."""

        return None

    def info(self) -> dict[str, object]:
        """Return cache diagnostics."""

        return {}
