from dataclasses import dataclass
from typing import Self

from ._parse import split_entity_ref
from .errors import SubjectValidationError
from .id import EntityId
from .namespace import NamespaceId
from .relation import Relation


@dataclass(frozen=True, slots=True)
class Subject:
    """
    Subject value object that can represent a direct subject or a subject set.

    Forms:
    - namespace:id
    - namespace:id#relation
    """

    namespace: NamespaceId
    id: EntityId
    relation: Relation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, NamespaceId):
            raise TypeError("subject namespace must be a NamespaceId")
        if not isinstance(self.id, EntityId):
            raise TypeError("subject id must be an EntityId")
        if self.relation is not None and not isinstance(self.relation, Relation):
            raise TypeError("subject relation must be a Relation or None")

    @classmethod
    def from_parts(
        cls,
        namespace: str,
        id: str,
        relation: str | None = None,
    ) -> Self:
        return cls(
            NamespaceId(namespace),
            EntityId(id),
            Relation(relation) if relation is not None else None,
        )

    @classmethod
    def from_object(cls, obj: object) -> Self:
        from .object import Obj

        if not isinstance(obj, Obj):
            raise TypeError("subject object source must be an Obj")
        return cls(obj.namespace, obj.id)

    @classmethod
    def from_string(cls, subject_string: str) -> Self:
        base, separator, rel = subject_string.partition("#")
        if separator != "" and rel == "":
            raise SubjectValidationError("subject_relation cannot be empty string")

        ns_str, id_str = split_entity_ref(
            base,
            kind="subject",
            error_type=SubjectValidationError,
        )
        return cls.from_parts(ns_str, id_str, rel if separator != "" else None)

    def to_dict(self) -> dict:
        return {
            "namespace": str(self.namespace),
            "id": str(self.id),
            "relation": str(self.relation) if self.relation is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        return cls.from_parts(
            data["namespace"],
            data["id"],
            data.get("relation"),
        )

    def require_direct(self) -> Self:
        if self.relation is not None:
            raise SubjectValidationError(
                "direct subject cannot include a subject relation"
            )
        return self

    def __str__(self) -> str:
        base = f"{self.namespace}:{self.id}"
        return f"{base}#{self.relation}" if self.relation is not None else base
