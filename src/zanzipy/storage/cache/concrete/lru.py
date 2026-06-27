"""In-memory LRU cache for tenant-scoped relation tuple buckets."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from zanzipy.storage.cache.abstract.tuples import TupleCache
from zanzipy.storage.cache.concrete._lru import LruStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from zanzipy.models import Obj, RelationTuple, Subject
    from zanzipy.storage.revision import ReadContext


@dataclass(frozen=True, slots=True)
class _ObjectKey:
    tenant_id: str
    namespace: str
    id: str
    revision: int


@dataclass(frozen=True, slots=True)
class _SubjectKey:
    tenant_id: str
    namespace: str
    id: str
    relation: str | None
    revision: int


type _TupleCacheKey = _ObjectKey | _SubjectKey


class LruTupleCache(TupleCache):
    """Thread-safe size-bounded LRU/TTL cache for tenant tuple buckets."""

    def __init__(
        self, *, max_entries: int = 10000, ttl_seconds: float | None = 30.0
    ) -> None:
        """Configure the in-memory capacity and optional TTL for each bucket."""
        self._store: LruStore[_TupleCacheKey, tuple[RelationTuple, ...]] = LruStore(
            max_entries=max_entries,
            ttl_seconds=ttl_seconds,
        )

    def get_by_object(
        self,
        obj: Obj,
        *,
        context: ReadContext,
    ) -> Sequence[RelationTuple] | None:
        """Return cached tuples for an object/context, or ``None`` on miss."""
        return self._store.get(_object_key(obj, context=context))

    def set_by_object(
        self,
        obj: Obj,
        *,
        context: ReadContext,
        tuples: Sequence[RelationTuple],
    ) -> None:
        """Cache an object bucket for the exact tenant revision key."""
        self._store.set(_object_key(obj, context=context), tuple(tuples))

    def get_by_subject(
        self,
        subject: Subject,
        *,
        context: ReadContext,
    ) -> Sequence[RelationTuple] | None:
        """Return cached tuples for a subject/context, or ``None`` on miss."""
        return self._store.get(_subject_key(subject, context=context))

    def set_by_subject(
        self,
        subject: Subject,
        *,
        context: ReadContext,
        tuples: Sequence[RelationTuple],
    ) -> None:
        """Cache a subject bucket for the exact tenant revision key."""
        self._store.set(_subject_key(subject, context=context), tuple(tuples))

    def ping(self) -> bool:
        """Return ``True`` because the in-memory cache has no dependency."""
        return True

    def close(self) -> None:
        """Clear in-memory entries and release cached tuple buckets."""
        self._store.clear()

    def info(self) -> dict[str, object]:
        """Return LRU diagnostics plus object and subject bucket counts."""
        info = self._store.info()
        info["size_objects"] = self._store.count_where(
            lambda key: isinstance(key, _ObjectKey)
        )
        info["size_subjects"] = self._store.count_where(
            lambda key: isinstance(key, _SubjectKey)
        )
        return info


def _object_key(obj: Obj, *, context: ReadContext) -> _ObjectKey:
    return _ObjectKey(
        tenant_id=str(context.tenant),
        namespace=str(obj.namespace),
        id=str(obj.id),
        revision=context.revision.value,
    )


def _subject_key(subject: Subject, *, context: ReadContext) -> _SubjectKey:
    return _SubjectKey(
        tenant_id=str(context.tenant),
        namespace=str(subject.namespace),
        id=str(subject.id),
        relation=None if subject.relation is None else str(subject.relation),
        revision=context.revision.value,
    )
