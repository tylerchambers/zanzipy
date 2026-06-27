from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from .id import EntityId
from .namespace import NamespaceId
from .object import Obj
from .relation import Relation
from .subject import Subject

if TYPE_CHECKING:
    from .tuple import RelationTuple


@dataclass(frozen=True, slots=True)
class TupleFilter:
    """Optional criteria for matching relation tuples.

    ``None`` leaves a field unconstrained; an empty ``subject_relation`` matches
    only direct subjects with no subject-set relation. Non-empty fields are
    validated as namespace, entity, or relation identifiers on construction.
    """

    DIRECT_SUBJECT_RELATION: ClassVar[str] = ""

    object_type: str | None = None
    object_id: str | None = None
    relation: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    subject_relation: str | None = None

    def __post_init__(self) -> None:
        if self.object_type is not None:
            NamespaceId(self.object_type)
        if self.object_id is not None:
            EntityId(self.object_id)
        if self.relation is not None:
            Relation(self.relation)
        if self.subject_type is not None:
            NamespaceId(self.subject_type)
        if self.subject_id is not None:
            EntityId(self.subject_id)
        if (
            self.subject_relation is not None
            and self.subject_relation != self.DIRECT_SUBJECT_RELATION
        ):
            Relation(self.subject_relation)

    @classmethod
    def _exact_subject_relation(cls, subject: Subject | None) -> str | None:
        if subject is None:
            return None
        if subject.relation is None:
            return cls.DIRECT_SUBJECT_RELATION
        return str(subject.relation)

    def matches(self, tuple: RelationTuple) -> bool:
        """Return whether a tuple satisfies every specified criterion."""
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
        if self.subject_relation is None:
            return True
        if self.subject_relation == self.DIRECT_SUBJECT_RELATION:
            return tuple.subject.relation is None
        return (
            tuple.subject.relation is not None
            and str(tuple.subject.relation) == self.subject_relation
        )

    @classmethod
    def from_object(cls, obj: Obj) -> TupleFilter:
        """Create a filter matching the given object (type and id)."""
        return cls(
            object_type=str(obj.namespace),
            object_id=str(obj.id),
        )

    @classmethod
    def from_relation(cls, relation: Relation) -> TupleFilter:
        """Create a filter matching the given relation name."""
        return cls(relation=str(relation))

    @classmethod
    def from_subject(cls, subject: Subject) -> TupleFilter:
        """Create a filter matching exactly the given subject."""
        return cls(
            subject_type=str(subject.namespace),
            subject_id=str(subject.id),
            subject_relation=cls._exact_subject_relation(subject),
        )

    @classmethod
    def from_subject_bucket(cls, subject: Subject) -> TupleFilter:
        """Create a broad reverse-lookup filter for a subject cache bucket."""
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
        relation: Relation | None = None,
        subject: Subject | None = None,
    ) -> TupleFilter:
        """Create a filter from any subset of object, relation, and subject."""
        return cls(
            object_type=str(obj.namespace) if obj is not None else None,
            object_id=str(obj.id) if obj is not None else None,
            relation=str(relation) if relation is not None else None,
            subject_type=str(subject.namespace) if subject is not None else None,
            subject_id=str(subject.id) if subject is not None else None,
            subject_relation=cls._exact_subject_relation(subject),
        )

    @property
    def object_ref(self) -> Obj | None:
        """Return the exact object value when both object fields are set."""
        if self.object_type is None or self.object_id is None:
            return None
        return Obj.from_parts(self.object_type, self.object_id)

    @property
    def subject_ref(self) -> Subject | None:
        """Return a subject value when subject type and id are set.

        An unconstrained or direct-only subject relation is represented as a
        direct subject with no relation.
        """
        if self.subject_type is None or self.subject_id is None:
            return None
        relation = (
            None
            if self.subject_relation in (None, self.DIRECT_SUBJECT_RELATION)
            else self.subject_relation
        )
        return Subject.from_parts(self.subject_type, self.subject_id, relation)

    @property
    def is_object_bucket(self) -> bool:
        """Return whether this filter selects an object cache bucket."""
        return (
            self.object_type is not None
            and self.object_id is not None
            and self.subject_type is None
            and self.subject_id is None
            and self.subject_relation is None
        )

    @property
    def is_subject_bucket(self) -> bool:
        """Return whether this filter selects a subject cache bucket."""
        return (
            self.subject_type is not None
            and self.subject_id is not None
            and self.object_type is None
            and self.object_id is None
        )

    def subject_bucket_filter(self) -> TupleFilter:
        """Return the broader subject-bucket filter for this subject.

        Raises:
            ValueError: If subject type or id is missing.
        """
        if self.subject_type is None or self.subject_id is None:
            raise ValueError("subject bucket filter requires subject type and id")
        return type(self)(
            subject_type=self.subject_type,
            subject_id=self.subject_id,
            subject_relation=(
                None
                if self.subject_relation == self.DIRECT_SUBJECT_RELATION
                else self.subject_relation
            ),
        )
