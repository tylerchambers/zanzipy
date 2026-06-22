"""Deterministic in-memory relation tuple repository."""

from collections.abc import Iterable  # noqa: TC003

from zanzipy.models.filter import TupleFilter  # noqa: TC001
from zanzipy.models.tuple import RelationTuple  # noqa: TC001
from zanzipy.storage.repos.abstract.relations import RelationRepository


class InMemoryRelationRepository(RelationRepository):
    """In-memory ``RelationRepository`` for tests and local prototypes.

    The repository preserves first-write order for deterministic reads while
    still treating the tuple's canonical string form as the idempotency key.
    It is not thread-safe.
    """

    def __init__(self) -> None:
        self._tuples: dict[str, RelationTuple] = {}

    def upsert(self, entity: RelationTuple) -> None:
        self._tuples[str(entity)] = entity

    def delete_by_key(self, key: RelationTuple) -> bool:
        return self._tuples.pop(str(key), None) is not None

    def get(self, key: RelationTuple) -> RelationTuple | None:
        return self._tuples.get(str(key))

    def read(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        return [tuple_ for tuple_ in self._tuples.values() if filter.matches(tuple_)]

    def read_reverse(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        return self.read(filter)
