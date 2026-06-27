"""In-memory LRU cache for revisioned relation tuple buckets."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from zanzipy.storage.cache.abstract.tuples import TupleCache
from zanzipy.storage.cache.concrete._lru import LruStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from zanzipy.models import Obj, RelationTuple, Subject
    from zanzipy.storage.revision import Revision


@dataclass(frozen=True, slots=True)
class _ObjectKey:
    namespace: str
    id: str
    revision: int


@dataclass(frozen=True, slots=True)
class _SubjectKey:
    namespace: str
    id: str
    relation: str | None
    revision: int


type _TupleCacheKey = _ObjectKey | _SubjectKey


class LruTupleCache(TupleCache):
    """Thread-safe size-bounded LRU/TTL cache for revisioned tuple buckets."""

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
        revision: Revision,
    ) -> Sequence[RelationTuple] | None:
        """Return cached tuples for an object/revision, or ``None`` on miss."""
        return self._store.get(_object_key(obj, revision=revision))

    def set_by_object(
        self,
        obj: Obj,
        *,
        revision: Revision,
        tuples: Sequence[RelationTuple],
    ) -> None:
        """Cache an object bucket for the exact revision key."""
        self._store.set(_object_key(obj, revision=revision), tuple(tuples))

    def get_by_subject(
        self,
        subject: Subject,
        *,
        revision: Revision,
    ) -> Sequence[RelationTuple] | None:
        """Return cached tuples for a subject/revision, or ``None`` on miss."""
        return self._store.get(_subject_key(subject, revision=revision))

    def set_by_subject(
        self,
        subject: Subject,
        *,
        revision: Revision,
        tuples: Sequence[RelationTuple],
    ) -> None:
        """Cache a subject bucket for the exact revision key."""
        self._store.set(_subject_key(subject, revision=revision), tuple(tuples))

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


def _object_key(obj: Obj, *, revision: Revision) -> _ObjectKey:
    return _ObjectKey(
        namespace=str(obj.namespace),
        id=str(obj.id),
        revision=revision.value,
    )


def _subject_key(subject: Subject, *, revision: Revision) -> _SubjectKey:
    return _SubjectKey(
        namespace=str(subject.namespace),
        id=str(subject.id),
        relation=None if subject.relation is None else str(subject.relation),
        revision=revision.value,
    )
