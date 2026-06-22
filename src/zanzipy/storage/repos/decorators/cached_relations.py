"""Read-through cache decorator for relation repositories."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from zanzipy.models.filter import TupleFilter
from zanzipy.models.object import Obj
from zanzipy.models.subject import Subject
from zanzipy.storage.repos.abstract.relations import RelationRepository

if TYPE_CHECKING:
    from collections.abc import Iterable

    from zanzipy.models.tuple import RelationTuple
    from zanzipy.storage.cache.abstract.tuples import TupleCache


@dataclass(slots=True)
class CachedRelationRepository(RelationRepository):
    """Decorate a relation repository with object and subject bucket caching.

    Cached buckets are deliberately broad: object buckets contain every tuple for
    an object, and subject buckets contain every tuple for a subject plus an
    optional subject relation. Every public read reapplies the original
    ``TupleFilter`` before returning results, so cache hits and backend reads keep
    identical semantics.
    """

    backend: RelationRepository
    cache: TupleCache

    def upsert(self, entity: RelationTuple) -> None:
        self.backend.upsert(entity)
        self._invalidate_entity(entity)

    def upsert_many(self, entities: Iterable[RelationTuple]) -> None:
        entities = list(entities)
        self.backend.upsert_many(entities)
        self._invalidate_from_entities(entities)

    def delete_by_key(self, key: RelationTuple) -> bool:
        old = self.backend.get(key)
        deleted = self.backend.delete_by_key(key)
        if deleted and old is not None:
            self._invalidate_entity(old)
        return deleted

    def delete_many_by_key(self, keys: Iterable[RelationTuple]) -> int:
        keys = list(keys)
        old_entities = [entity for key in keys if (entity := self.backend.get(key))]
        deleted = self.backend.delete_many_by_key(keys)
        self._invalidate_from_entities(old_entities)
        return deleted

    def by_object(self, obj: Obj) -> Iterable[RelationTuple]:
        return self._object_bucket(obj)

    def read(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        if self._is_object_bucket_filter(filter):
            obj = Obj.from_string(f"{filter.object_type}:{filter.object_id}")
            return [
                tuple_ for tuple_ in self._object_bucket(obj) if filter.matches(tuple_)
            ]

        if self._is_subject_bucket_filter(filter):
            subject = self._subject_from_filter(filter)
            cached_filter = TupleFilter(
                subject_type=filter.subject_type,
                subject_id=filter.subject_id,
                subject_relation=filter.subject_relation,
            )
            tuples = self._subject_bucket(subject, cached_filter)
            return [tuple_ for tuple_ in tuples if filter.matches(tuple_)]

        return self.backend.read(filter)

    def read_reverse(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        if self._is_subject_bucket_filter(filter):
            return self.read(filter)
        return self.backend.read_reverse(filter)

    def find(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        return self.read(filter)

    def key_of(self, entity: RelationTuple) -> RelationTuple:
        return self.backend.key_of(entity)

    def get(self, key: RelationTuple) -> RelationTuple | None:
        return self.backend.get(key)

    def ping(self) -> bool:
        return self.backend.ping() and self.cache.ping()

    def info(self) -> dict[str, object]:
        return {
            "decorator": "CachedRelationRepository",
            "backend": self.backend.info(),
            "cache": self.cache.info(),
        }

    def close(self) -> None:
        try:
            self.backend.close()
        finally:
            self.cache.close()

    def _object_bucket(self, obj: Obj) -> list[RelationTuple]:
        cached = self.cache.get_by_object(obj)
        if cached is not None:
            return list(cached)
        result = list(self.backend.by_object(obj))
        self.cache.set_by_object(obj, result)
        return result

    def _subject_bucket(
        self, subject: Subject, filter: TupleFilter
    ) -> list[RelationTuple]:
        cached = self.cache.get_by_subject(subject)
        if cached is not None:
            return list(cached)
        result = list(self.backend.read_reverse(filter))
        self.cache.set_by_subject(subject, result)
        return result

    @staticmethod
    def _is_object_bucket_filter(filter: TupleFilter) -> bool:
        return (
            filter.object_type is not None
            and filter.object_id is not None
            and filter.subject_type is None
            and filter.subject_id is None
            and filter.subject_relation is None
        )

    @staticmethod
    def _is_subject_bucket_filter(filter: TupleFilter) -> bool:
        return (
            filter.subject_type is not None
            and filter.subject_id is not None
            and filter.object_type is None
            and filter.object_id is None
        )

    @staticmethod
    def _subject_from_filter(filter: TupleFilter) -> Subject:
        suffix = (
            f"#{filter.subject_relation}" if filter.subject_relation is not None else ""
        )
        return Subject.from_string(f"{filter.subject_type}:{filter.subject_id}{suffix}")

    def _invalidate_from_entities(self, entities: Iterable[RelationTuple]) -> None:
        seen_objects: set[Obj] = set()
        seen_subjects: set[Subject] = set()
        for entity in entities:
            if entity.object not in seen_objects:
                self.cache.invalidate_object(entity.object)
                seen_objects.add(entity.object)

            for subject in self._subject_invalidation_keys(entity.subject):
                if subject not in seen_subjects:
                    self.cache.invalidate_subject(subject)
                    seen_subjects.add(subject)

    def _invalidate_entity(self, entity: RelationTuple) -> None:
        self.cache.invalidate_object(entity.object)
        for subject in self._subject_invalidation_keys(entity.subject):
            self.cache.invalidate_subject(subject)

    @staticmethod
    def _subject_invalidation_keys(subject: Subject) -> tuple[Subject, ...]:
        if subject.relation is None:
            return (subject,)
        broad_subject = Subject(subject.namespace, subject.id)
        return (subject, broad_subject)
