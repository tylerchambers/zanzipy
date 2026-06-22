from dataclasses import dataclass
from typing import Self

from .errors import (
    SubjectValidationError,
)
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

    @classmethod
    def from_string(cls, subject_string: str) -> Self:
        # Split only once on '#'
        if "#" in subject_string:
            base, rel = subject_string.split("#", 1)
            if rel == "":
                raise SubjectValidationError("subject_relation cannot be empty string")
            relation = Relation(rel)
        else:
            base = subject_string
            relation = None

        if ":" not in base:
            raise SubjectValidationError("subject must be in 'namespace:id' form")
        ns_str, id_str = base.split(":", 1)

        namespace = NamespaceId(ns_str)
        entity_id = EntityId(id_str)

        return cls(namespace, entity_id, relation)

    def to_dict(self) -> dict:
        return {
            "namespace": str(self.namespace),
            "id": str(self.id),
            "relation": str(self.relation) if self.relation is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        return cls(
            NamespaceId(data["namespace"]),
            EntityId(data["id"]),
            Relation(data["relation"]) if data["relation"] else None,
        )

    def __str__(self) -> str:
        base = f"{self.namespace}:{self.id}"
        return f"{base}#{self.relation}" if self.relation is not None else base
