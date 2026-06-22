"""Abstract repository for Zanzibar relation tuples.

Defines an abstract interface specialized for storing and querying
``RelationTuple`` entries, building on the generic ``BaseRepository``.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from zanzipy.models.filter import TupleFilter as _TupleFilter

from .base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Iterable

    from zanzipy.models.object import Obj
    from zanzipy.models.relation import Relation as Rel
    from zanzipy.models.subject import Subject


class RelationRepository[TRelationTuple, TFilter](
    BaseRepository[TRelationTuple, TRelationTuple, TFilter], ABC
):
    """Abstract storage interface for Zanzibar relation tuples.

    Type parameters:
    - ``TRelationTuple``: the relation tuple entity type
    - ``TFilter``: filter type for querying (e.g., ``TupleFilter``)

    Key model: the tuple itself is the key (identity is structural).
    """

    def key_of(self, entity: TRelationTuple) -> TRelationTuple:
        """Use the tuple entity itself as its key (structural identity)."""

        return entity

    def write(self, relation_tuple: TRelationTuple) -> None:
        """Store a relation tuple (idempotent)."""

        self.upsert(relation_tuple)

    def write_many(self, tuples: Iterable[TRelationTuple]) -> None:
        """Bulk write operation (override for performance)."""

        self.upsert_many(tuples)

    def delete(self, relation_tuple: TRelationTuple) -> bool:
        """Remove a relation tuple (idempotent). Returns True if deleted."""

        return super().delete(relation_tuple)

    def delete_many(self, tuples: Iterable[TRelationTuple]) -> int:
        """Bulk delete operation. Returns the number of deleted tuples."""

        return super().delete_many(tuples)

    @abstractmethod
    def read(self, filter: TFilter) -> Iterable[TRelationTuple]:
        """Forward lookup by object ("Who can access this resource?")."""

    @abstractmethod
    def read_reverse(self, filter: TFilter) -> Iterable[TRelationTuple]:
        """Reverse lookup by subject ("What can this subject access?")."""

    # Satisfy BaseRepository.find via forward reads by default.
    def find(self, filter: TFilter) -> Iterable[TRelationTuple]:
        return self.read(filter)

    def by_object(self, obj: Obj) -> Iterable[TRelationTuple]:
        """Iterate tuples for a specific object (type and id)."""

        return self.read(_TupleFilter.from_object(obj))

    def by_subject(self, subject: Subject) -> Iterable[TRelationTuple]:
        """Iterate tuples for a specific subject (reverse lookup)."""

        return self.read_reverse(_TupleFilter.from_subject(subject))

    def by_relation(self, relation: Rel) -> Iterable[TRelationTuple]:
        """Iterate tuples that have a specific relation name."""

        return self.read(_TupleFilter.from_relation(relation))
