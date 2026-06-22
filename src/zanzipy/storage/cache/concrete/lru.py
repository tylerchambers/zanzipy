"""In-memory LRU cache for relation tuples.

Thread-safe, size-bounded LRU with optional per-entry TTL. Stores pre-materialized
sequences of relation tuples keyed by object or subject, as defined by the
``TupleCache`` ABC.
"""

from collections import OrderedDict
from dataclasses import dataclass
import threading
import time
from typing import TYPE_CHECKING

from zanzipy.storage.cache.abstract.tuples import TupleCache

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


@dataclass(slots=True)
class _Entry:
    value: list[RelationTuple]
    expires_at: float | None


class LruTupleCache(TupleCache):
    """Simple LRU/TTL cache for relation tuples.

    Parameters:
        max_entries: maximum total entries across object and subject maps
        ttl_seconds: optional per-entry TTL in seconds (None means no TTL)

    Notes:
        - Values are stored as lists and returned as lists to avoid surprising
          aliasing of internal state; callers can treat them as sequences.
        - Expired entries are purged opportunistically on access or insert.
    """

    def __init__(
        self, *, max_entries: int = 10000, ttl_seconds: float | None = 30.0
    ) -> None:
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._lock = threading.RLock()
        # Separate LRU maps for objects and subjects
        self._obj_map: OrderedDict[_ObjectKey, _Entry] = OrderedDict()
        self._subj_map: OrderedDict[_SubjectKey, _Entry] = OrderedDict()
        # Counters
        self._hits = 0
        self._misses = 0

    def _now(self) -> float:
        return time.monotonic()

    def _is_expired(self, entry: _Entry, now: float) -> bool:
        return entry.expires_at is not None and now >= entry.expires_at

    def _touch(self, od: OrderedDict, key) -> None:
        od.move_to_end(key, last=True)

    def _evict_if_needed(self) -> None:
        total = len(self._obj_map) + len(self._subj_map)
        while total > self._max_entries:
            # Evict from the larger map first for fairness
            if len(self._obj_map) >= len(self._subj_map) and self._obj_map:
                self._obj_map.popitem(last=False)
            elif self._subj_map:
                self._subj_map.popitem(last=False)
            total = len(self._obj_map) + len(self._subj_map)

    def _expiry(self, now: float) -> float | None:
        return None if self._ttl is None else now + self._ttl

    def get_by_object(self, obj: Obj) -> Sequence[RelationTuple] | None:
        key = _ObjectKey(namespace=str(obj.namespace), id=str(obj.id))
        with self._lock:
            entry = self._obj_map.get(key)
            if entry is None:
                self._misses += 1
                return None
            now = self._now()
            if self._is_expired(entry, now):
                self._obj_map.pop(key, None)
                self._misses += 1
                return None
            self._touch(self._obj_map, key)
            self._hits += 1
            return list(entry.value)

    def set_by_object(self, obj: Obj, tuples: Sequence[RelationTuple]) -> None:
        key = _ObjectKey(namespace=str(obj.namespace), id=str(obj.id))
        with self._lock:
            entry = _Entry(value=list(tuples), expires_at=self._expiry(self._now()))
            self._obj_map[key] = entry
            self._touch(self._obj_map, key)
            self._evict_if_needed()

    def invalidate_object(self, obj: Obj) -> None:
        key = _ObjectKey(namespace=str(obj.namespace), id=str(obj.id))
        with self._lock:
            self._obj_map.pop(key, None)

    def get_by_subject(self, subject: Subject) -> Sequence[RelationTuple] | None:
        key = _SubjectKey(
            namespace=str(subject.namespace),
            id=str(subject.id),
            relation=(None if subject.relation is None else str(subject.relation)),
        )
        with self._lock:
            entry = self._subj_map.get(key)
            if entry is None:
                self._misses += 1
                return None
            now = self._now()
            if self._is_expired(entry, now):
                self._subj_map.pop(key, None)
                self._misses += 1
                return None
            self._touch(self._subj_map, key)
            self._hits += 1
            return list(entry.value)

    def set_by_subject(self, subject: Subject, tuples: Sequence[RelationTuple]) -> None:
        key = _SubjectKey(
            namespace=str(subject.namespace),
            id=str(subject.id),
            relation=(None if subject.relation is None else str(subject.relation)),
        )
        with self._lock:
            entry = _Entry(value=list(tuples), expires_at=self._expiry(self._now()))
            self._subj_map[key] = entry
            self._touch(self._subj_map, key)
            self._evict_if_needed()

    def invalidate_subject(self, subject: Subject) -> None:
        key = _SubjectKey(
            namespace=str(subject.namespace),
            id=str(subject.id),
            relation=(None if subject.relation is None else str(subject.relation)),
        )
        with self._lock:
            self._subj_map.pop(key, None)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        with self._lock:
            self._obj_map.clear()
            self._subj_map.clear()

    def info(self) -> dict[str, object]:
        with self._lock:
            return {
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl,
                "size_objects": len(self._obj_map),
                "size_subjects": len(self._subj_map),
                "hits": self._hits,
                "misses": self._misses,
            }
