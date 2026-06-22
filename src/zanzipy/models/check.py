from dataclasses import dataclass
from typing import Self

from .filter import TupleFilter
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
        self.to_object()
        self.to_relation()
        self.to_subject()

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
        subject.require_direct()
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
        return cls.from_parts(
            Obj.from_string(object_str),
            Relation(relation),
            Subject.from_string(subject_str),
        )

    def to_object(self) -> Obj:
        return Obj.from_parts(self.object_type, self.object_id)

    def to_relation(self) -> Relation:
        return Relation(self.relation)

    def to_subject(self) -> Subject:
        return Subject.from_parts(self.subject_type, self.subject_id)

    def to_filter(self) -> TupleFilter:
        """Convert to an exact TupleFilter for this direct check tuple."""
        return TupleFilter.from_parts(
            obj=self.to_object(),
            relation=self.to_relation(),
            subject=self.to_subject(),
        )


@dataclass(frozen=True, slots=True)
class CheckResponse:
    """Response with permission decision and optional debug info"""

    allowed: bool
    debug_trace: list[str] | None = None
    depth_reached: int = 0
    tuples_examined: int = 0
