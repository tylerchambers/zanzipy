from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .object import Obj
    from .relation import Relation as Rel
    from .subject import Subject
    from .tuple import RelationTuple


@dataclass(frozen=True, slots=True)
class TupleFilter:
    """
    Filter criteria for querying relation tuples.
    All fields are optional - only specified fields are used for filtering.
    """

    object_type: str | None = None
    object_id: str | None = None
    relation: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    subject_relation: str | None = None

    def matches(self, tuple: RelationTuple) -> bool:
        """Check if a tuple matches this filter."""
        return (
            self._matches_object(tuple)
            and self._matches_relation(tuple)
            and self._matches_subject(tuple)
        )

    def _matches_object(self, tuple: RelationTuple) -> bool:
        if (
            self.object_type is not None
            and str(tuple.object.namespace) != self.object_type
        ):
            return False
        return not (
            self.object_id is not None and str(tuple.object.id) != self.object_id
        )

    def _matches_relation(self, tuple: RelationTuple) -> bool:
        return not (self.relation is not None and str(tuple.relation) != self.relation)

    def _matches_subject(self, tuple: RelationTuple) -> bool:
        if (
            self.subject_type is not None
            and str(tuple.subject.namespace) != self.subject_type
        ):
            return False
        if self.subject_id is not None and str(tuple.subject.id) != self.subject_id:
            return False
        if self.subject_relation is not None:
            if tuple.subject.relation is None:
                return False
            if str(tuple.subject.relation) != self.subject_relation:
                return False
        return True

    @classmethod
    def from_object(cls, obj: Obj) -> TupleFilter:
        """Create a filter matching the given object (type and id)."""
        return cls(
            object_type=str(obj.namespace),
            object_id=str(obj.id),
        )

    @classmethod
    def from_relation(cls, relation: Rel) -> TupleFilter:
        """Create a filter matching the given relation name."""
        return cls(relation=str(relation))

    @classmethod
    def from_subject(cls, subject: Subject) -> TupleFilter:
        """Create a filter matching the given subject (type/id and optional rel)."""
        return cls(
            subject_type=str(subject.namespace),
            subject_id=str(subject.id),
            subject_relation=(
                str(subject.relation) if subject.relation is not None else None
            ),
        )

    @classmethod
    def from_parts(
        cls,
        obj: Obj | None = None,
        relation: Rel | None = None,
        subject: Subject | None = None,
    ) -> TupleFilter:
        """Create a filter from any subset of object, relation, and subject."""
        return cls(
            object_type=str(obj.namespace) if obj is not None else None,
            object_id=str(obj.id) if obj is not None else None,
            relation=str(relation) if relation is not None else None,
            subject_type=str(subject.namespace) if subject is not None else None,
            subject_id=str(subject.id) if subject is not None else None,
            subject_relation=(
                str(subject.relation)
                if subject is not None and subject.relation is not None
                else None
            ),
        )
