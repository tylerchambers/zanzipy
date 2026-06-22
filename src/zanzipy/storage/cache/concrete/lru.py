"""In-memory LRU cache for relation tuple buckets."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from zanzipy.storage.cache.abstract.tuples import TupleCache
from zanzipy.storage.cache.concrete._lru import LruStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from zanzipy.models.object import Obj
    from zanzipy.models.subject import Subject
    from zanzipy.models.tuple import RelationTuple


@dataclass(frozen=True, slots=True)
class _ObjectKey:
    namespace: str
    id: str


@dataclass(frozen=True, slots=True)
class _SubjectKey:
    namespace: str
    id: str
    relation: str | None


type _TupleCacheKey = _ObjectKey | _SubjectKey


class LruTupleCache(TupleCache):
    """Thread-safe size-bounded LRU/TTL cache for relation tuple buckets.

    ``max_entries`` is global across object and subject buckets. Cached values
    are stored and returned as immutable tuples to avoid aliasing cached state.
    """

    def __init__(
        self, *, max_entries: int = 10000, ttl_seconds: float | None = 30.0
    ) -> None:
        self._store: LruStore[_TupleCacheKey, tuple[RelationTuple, ...]] = LruStore(
            max_entries=max_entries,
            ttl_seconds=ttl_seconds,
        )

    def get_by_object(self, obj: Obj) -> Sequence[RelationTuple] | None:
        return self._store.get(_object_key(obj))

    def set_by_object(self, obj: Obj, tuples: Sequence[RelationTuple]) -> None:
        self._store.set(_object_key(obj), tuple(tuples))

    def invalidate_object(self, obj: Obj) -> None:
        self._store.delete(_object_key(obj))

    def get_by_subject(self, subject: Subject) -> Sequence[RelationTuple] | None:
        return self._store.get(_subject_key(subject))

    def set_by_subject(self, subject: Subject, tuples: Sequence[RelationTuple]) -> None:
        self._store.set(_subject_key(subject), tuple(tuples))

    def invalidate_subject(self, subject: Subject) -> None:
        self._store.delete(_subject_key(subject))

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        self._store.clear()

    def info(self) -> dict[str, object]:
        info = self._store.info()
        info["size_objects"] = self._store.count_where(
            lambda key: isinstance(key, _ObjectKey)
        )
        info["size_subjects"] = self._store.count_where(
            lambda key: isinstance(key, _SubjectKey)
        )
        return info


def _object_key(obj: Obj) -> _ObjectKey:
    return _ObjectKey(namespace=str(obj.namespace), id=str(obj.id))


def _subject_key(subject: Subject) -> _SubjectKey:
    return _SubjectKey(
        namespace=str(subject.namespace),
        id=str(subject.id),
        relation=None if subject.relation is None else str(subject.relation),
    )
