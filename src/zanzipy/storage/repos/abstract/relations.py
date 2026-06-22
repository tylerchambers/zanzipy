"""Storage contract for Zanzibar relation tuples.

The storage layer has one canonical key and one canonical filter:
``RelationTuple`` is the structural key, and ``TupleFilter`` describes every
forward or reverse lookup. Concrete repositories can index those fields however
they want, but they should not expose backend-specific key objects or alternate
filter shapes through this interface.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Self

from zanzipy.models import TupleFilter

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from zanzipy.models import Obj, Relation, RelationTuple, Subject


class RelationRepository(ABC):
    """Abstract repository for durable ``RelationTuple`` storage.

    Implementations must treat writes as idempotent and use the tuple's
    canonical string form as structural identity. ``read`` and ``read_reverse``
    both honor every field supplied by ``TupleFilter``; the reverse method exists
    so backends can use subject-oriented indexes for graph traversals.
    """

    def key_of(self, entity: RelationTuple) -> RelationTuple:
        """Return the public repository key for ``entity``."""

        return entity

    @abstractmethod
    def upsert(self, entity: RelationTuple) -> None:
        """Insert ``entity`` if it is absent; otherwise leave storage unchanged."""

    def upsert_many(self, entities: Iterable[RelationTuple]) -> None:
        """Insert each tuple from ``entities`` idempotently."""

        for entity in entities:
            self.upsert(entity)

    def write(self, relation_tuple: RelationTuple) -> None:
        """Store ``relation_tuple`` idempotently."""

        self.upsert(relation_tuple)

    def write_many(self, tuples: Iterable[RelationTuple]) -> None:
        """Store each tuple from ``tuples`` idempotently."""

        self.upsert_many(tuples)

    @abstractmethod
    def delete_by_key(self, key: RelationTuple) -> bool:
        """Delete ``key`` and return whether a stored tuple was removed."""

    def delete(self, relation_tuple: RelationTuple) -> bool:
        """Delete ``relation_tuple`` and return whether it existed."""

        return self.delete_by_key(self.key_of(relation_tuple))

    def delete_many_by_key(self, keys: Iterable[RelationTuple]) -> int:
        """Delete each key and return the number of stored tuples removed."""

        deleted = 0
        for key in keys:
            deleted += int(self.delete_by_key(key))
        return deleted

    def delete_many(self, tuples: Iterable[RelationTuple]) -> int:
        """Delete each tuple and return the number of stored tuples removed."""

        return self.delete_many_by_key(self.key_of(tuple_) for tuple_ in tuples)

    def delete_where(self, filter: TupleFilter) -> int:
        """Delete all tuples matching ``filter`` and return the removed count."""

        matches = list(self.read(filter))
        return self.delete_many_by_key(matches)

    @abstractmethod
    def get(self, key: RelationTuple) -> RelationTuple | None:
        """Return ``key`` if it is currently stored, otherwise ``None``."""

    def exists(self, key: RelationTuple) -> bool:
        """Return whether ``key`` is currently stored."""

        return self.get(key) is not None

    @abstractmethod
    def read(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        """Return tuples matching ``filter`` using the backend's forward path."""

    @abstractmethod
    def read_reverse(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        """Return tuples matching ``filter`` using the backend's reverse path."""

    def find(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        """Alias for ``read`` for callers that prefer repository terminology."""

        return self.read(filter)

    def iter(self, filter: TupleFilter) -> Iterator[RelationTuple]:
        """Yield tuples matching ``filter``."""

        yield from self.read(filter)

    def by_object(self, obj: Obj) -> Iterable[RelationTuple]:
        """Return tuples for ``obj`` regardless of relation or subject."""

        return self.read(TupleFilter.from_object(obj))

    def by_subject(self, subject: Subject) -> Iterable[RelationTuple]:
        """Return tuples for ``subject`` using the reverse lookup path."""

        return self.read_reverse(TupleFilter.from_subject(subject))

    def by_relation(self, relation: Relation) -> Iterable[RelationTuple]:
        """Return tuples with relation name ``relation``."""

        return self.read(TupleFilter.from_relation(relation))

    def ping(self) -> bool:
        """Return whether the repository is reachable."""

        return True

    def info(self) -> dict[str, Any]:
        """Return backend diagnostics."""

        return {}

    def close(self) -> None:
        """Release backend resources."""

        return None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
        return None
