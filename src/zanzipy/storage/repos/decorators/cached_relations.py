"""Cached RelationRepository decorator using a TupleCache.

Wraps a backing RelationRepository and provides read-through caching for
common forward/reverse lookups, with targeted invalidation on writes/deletes.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from zanzipy.models.object import Obj
from zanzipy.models.subject import Subject
from zanzipy.models.tuple import RelationTuple
from zanzipy.storage.repos.abstract.relations import RelationRepository

if TYPE_CHECKING:
    from collections.abc import Iterable

    from zanzipy.storage.cache.abstract.tuples import TupleCache


@dataclass(slots=True)
class CachedRelationRepository(RelationRepository[RelationTuple, Any]):
    backend: RelationRepository[RelationTuple, Any]
    cache: TupleCache

    def upsert(self, entity: RelationTuple) -> None:
        self.backend.upsert(entity)
        # Invalidate impacted keys
        self.cache.invalidate_object(entity.object)
        self.cache.invalidate_subject(entity.subject)

    def upsert_many(self, entities: Iterable[RelationTuple]) -> None:
        entities = list(entities)
        self.backend.upsert_many(entities)
        self._invalidate_from_entities(entities)

    def delete_by_key(self, key: RelationTuple) -> bool:
        # Fetch entity first (best-effort) for precise invalidation
        old = self.backend.get(key)
        deleted = self.backend.delete_by_key(key)
        if deleted and old is not None:
            self.cache.invalidate_object(old.object)
            self.cache.invalidate_subject(old.subject)
        return deleted

    def delete_many_by_key(self, keys: Iterable[RelationTuple]) -> int:
        olds = [self.backend.get(k) for k in keys]
        deleted = self.backend.delete_many_by_key(keys)
        self._invalidate_from_entities([e for e in olds if e is not None])
        return deleted

    def by_object(self, obj: Obj) -> Iterable[RelationTuple]:
        cached = self.cache.get_by_object(obj)
        if cached is not None:
            return list(cached)
        result = list(self.backend.by_object(obj))
        self.cache.set_by_object(obj, result)
        return list(result)

    def read(self, filter: Any) -> Iterable[RelationTuple]:
        # Object-scoped forward read (duck-typed attrs)
        object_type = getattr(filter, "object_type", None)
        object_id = getattr(filter, "object_id", None)
        subject_type = getattr(filter, "subject_type", None)
        subject_id = getattr(filter, "subject_id", None)
        relation = getattr(filter, "relation", None)
        subject_relation = getattr(filter, "subject_relation", None)

        if (
            object_type is not None
            and object_id is not None
            and subject_type is None
            and subject_id is None
        ):
            obj = Obj.from_string(f"{object_type}:{object_id}")
            tuples = self.by_object(obj)
            if relation is not None:
                tuples = [t for t in tuples if str(t.relation) == relation]
            return tuples

        # Subject-scoped reverse read
        if (
            subject_type is not None
            and subject_id is not None
            and object_type is None
            and object_id is None
        ):
            suffix = f"#{subject_relation}" if subject_relation is not None else ""
            subject = Subject.from_string(f"{subject_type}:{subject_id}{suffix}")
            cached = self.cache.get_by_subject(subject)
            if cached is not None:
                return list(cached)
            result = list(self.backend.read_reverse(filter))
            self.cache.set_by_subject(subject, result)
            return list(result)

        # Mixed/broad queries fall back to backend
        return self.backend.read(filter)

    def read_reverse(self, filter: Any) -> Iterable[RelationTuple]:
        # Delegate to read() which already handles reverse caching
        return self.read(filter)

    def find(self, filter: Any) -> Iterable[RelationTuple]:
        return self.read(filter)

    def key_of(self, entity: RelationTuple) -> RelationTuple:
        return self.backend.key_of(entity)

    def get(self, key: RelationTuple) -> RelationTuple | None:
        return self.backend.get(key)

    def ping(self) -> bool:
        return bool(self.backend.ping() and self.cache.ping())

    def info(self) -> dict[str, object]:
        info = {"decorator": "CachedRelationRepository"}
        try:
            info["cache"] = self.cache.info()
        except Exception:
            info["cache"] = {}
        try:
            info["backend"] = self.backend.info()
        except Exception:
            info["backend"] = {}
        return info

    def close(self) -> None:
        try:
            self.backend.close()
        finally:
            self.cache.close()

    def _invalidate_from_entities(self, entities: Iterable[RelationTuple]) -> None:
        seen_obj: set[str] = set()
        seen_subj: set[str] = set()
        for e in entities:
            obj_key = f"{e.object.namespace}:{e.object.id}"
            if obj_key not in seen_obj:
                self.cache.invalidate_object(e.object)
                seen_obj.add(obj_key)
            subj_key = (
                f"{e.subject.namespace}:{e.subject.id}#{e.subject.relation}"
                if e.subject.relation is not None
                else f"{e.subject.namespace}:{e.subject.id}"
            )
            if subj_key not in seen_subj:
                self.cache.invalidate_subject(e.subject)
                seen_subj.add(subj_key)
