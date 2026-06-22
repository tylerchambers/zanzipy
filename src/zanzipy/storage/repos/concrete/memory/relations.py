from dataclasses import dataclass
from typing import TYPE_CHECKING

from zanzipy.models.tuple import RelationTuple
from zanzipy.storage.repos.abstract.relations import RelationRepository

if TYPE_CHECKING:
    from collections.abc import Iterable

    from zanzipy.models.filter import TupleFilter


@dataclass(frozen=True, slots=True)
class MemoryTupleFilter:
    """Filter used by the in-memory relation repository.

    Mirrors ``TupleFilter`` so this backend can be drop-in for tests.
    """

    object_type: str | None = None
    object_id: str | None = None
    relation: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    subject_relation: str | None = None


class InMemoryRelationRepository(RelationRepository[RelationTuple, MemoryTupleFilter]):
    """In-memory implementation of RelationRepository.

    Not thread-safe; intended for tests and local prototypes.
    """

    def __init__(self) -> None:
        self._items: set[RelationTuple] = set()

    def upsert(self, entity: RelationTuple) -> None:
        self._items.add(entity)

    def delete_by_key(self, key: RelationTuple) -> bool:
        try:
            self._items.remove(key)
            return True
        except KeyError:
            return False

    def get(self, key: RelationTuple) -> RelationTuple | None:
        return key if key in self._items else None

    def find(self, filter: MemoryTupleFilter) -> Iterable[RelationTuple]:
        for t in self._items:
            if (
                filter.object_type is not None
                and str(t.object.namespace) != filter.object_type
            ):
                continue
            if filter.object_id is not None and str(t.object.id) != filter.object_id:
                continue
            if filter.relation is not None and str(t.relation) != filter.relation:
                continue
            if (
                filter.subject_type is not None
                and str(t.subject.namespace) != filter.subject_type
            ):
                continue
            if filter.subject_id is not None and str(t.subject.id) != filter.subject_id:
                continue
            if filter.subject_relation is not None:
                if t.subject.relation is None:
                    continue
                if str(t.subject.relation) != filter.subject_relation:
                    continue
            yield t

    def read(self, filter: MemoryTupleFilter) -> Iterable[RelationTuple]:
        return self.find(filter)

    def read_reverse(self, filter: MemoryTupleFilter) -> Iterable[RelationTuple]:
        for t in self._items:
            if (
                filter.subject_type is not None
                and str(t.subject.namespace) != filter.subject_type
            ):
                continue
            if filter.subject_id is not None and str(t.subject.id) != filter.subject_id:
                continue
            if filter.subject_relation is not None:
                if t.subject.relation is None:
                    continue
                if str(t.subject.relation) != filter.subject_relation:
                    continue
            yield t

    def by_object_filter(self, tuple_filter: TupleFilter) -> MemoryTupleFilter:
        return MemoryTupleFilter(
            object_type=tuple_filter.object_type,
            object_id=tuple_filter.object_id,
        )
