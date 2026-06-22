from dataclasses import dataclass
from typing import Self

from .filter import TupleFilter
from .id import EntityId
from .namespace import NamespaceId
from .object import Obj
from .relation import Relation
from .subject import Subject


@dataclass(frozen=True, slots=True)
class CheckRequest:
    """Request to check if subject has relation to object"""

    object_type: str
    object_id: str
    relation: str
    subject_type: str
    subject_id: str

    def __post_init__(self) -> None:
        # Validate components using existing value objects
        NamespaceId(self.object_type)
        EntityId(self.object_id)
        Relation(self.relation)
        NamespaceId(self.subject_type)
        EntityId(self.subject_id)

    @property
    def object(self) -> str:
        return f"{self.object_type}:{self.object_id}"

    @property
    def subject(self) -> str:
        return f"{self.subject_type}:{self.subject_id}"

    def __str__(self) -> str:
        return f"{self.object}#{self.relation}@{self.subject}"

    def to_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "object_id": self.object_id,
            "relation": self.relation,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        return cls(
            object_type=data["object_type"],
            object_id=data["object_id"],
            relation=data["relation"],
            subject_type=data["subject_type"],
            subject_id=data["subject_id"],
        )

    @classmethod
    def from_parts(cls, obj: Obj, relation: Relation, subject: Subject) -> Self:
        """Construct from domain objects.

        Requires a direct subject (no subject relation on the subject set).
        """
        if subject.relation is not None:
            raise ValueError(
                "CheckRequest requires a direct subject (no subject relation)"
            )
        return cls(
            object_type=str(obj.namespace),
            object_id=str(obj.id),
            relation=str(relation),
            subject_type=str(subject.namespace),
            subject_id=str(subject.id),
        )

    @classmethod
    def from_strings(cls, object_str: str, relation: str, subject_str: str) -> Self:
        """Construct from 'ns:id', relation, and 'ns:id' strings.

        The subject must be direct (no '#relation' allowed).
        """
        if ":" not in object_str:
            raise ValueError("object must be in 'namespace:id' form")
        obj_ns, obj_id = object_str.split(":", 1)
        # Validate using value objects
        obj_namespace = NamespaceId(obj_ns)
        obj_entity_id = EntityId(obj_id)

        # Reuse Subject.parse to leverage validation and detect subject sets
        subject = Subject.from_string(subject_str)
        if subject.relation is not None:
            raise ValueError(
                "CheckRequest requires a direct subject (no subject relation)"
            )

        return cls(
            object_type=str(obj_namespace),
            object_id=str(obj_entity_id),
            relation=str(Relation(relation)),
            subject_type=str(subject.namespace),
            subject_id=str(subject.id),
        )

    def to_object(self) -> Obj:
        return Obj(NamespaceId(self.object_type), EntityId(self.object_id))

    def to_relation(self) -> Relation:
        return Relation(self.relation)

    def to_subject(self) -> Subject:
        return Subject(NamespaceId(self.subject_type), EntityId(self.subject_id))

    def to_filter(self) -> TupleFilter:
        """Convert to a TupleFilter for backing tuple lookups."""
        return TupleFilter(
            object_type=self.object_type,
            object_id=self.object_id,
            relation=self.relation,
            subject_type=self.subject_type,
            subject_id=self.subject_id,
        )


@dataclass(frozen=True, slots=True)
class CheckResponse:
    """Response with permission decision and optional debug info"""

    allowed: bool
    debug_trace: list[str] | None = None
    depth_reached: int = 0
    tuples_examined: int = 0
