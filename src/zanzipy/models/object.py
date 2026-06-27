from dataclasses import dataclass
from typing import Self

from ._parse import split_entity_ref
from .errors import ObjectValidationError
from .id import EntityId
from .namespace import NamespaceId


@dataclass(frozen=True, slots=True)
class Obj:
    """Immutable object reference with canonical ``namespace:id`` form."""

    namespace: NamespaceId
    id: EntityId

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, NamespaceId):
            raise TypeError("object namespace must be a NamespaceId")
        if not isinstance(self.id, EntityId):
            raise TypeError("object id must be an EntityId")

    def __str__(self) -> str:
        return f"{self.namespace}:{self.id}"

    @classmethod
    def from_parts(cls, namespace: str, id: str) -> Self:
        """Create an object reference from namespace and entity id strings."""
        return cls(NamespaceId(namespace), EntityId(id))

    @classmethod
    def from_string(cls, object_string: str) -> Self:
        """Parse the canonical ``namespace:id`` object string.

        Raises:
            ObjectValidationError: If the string is not a valid object reference.
        """
        ns_str, id_str = split_entity_ref(
            object_string,
            kind="object",
            error_type=ObjectValidationError,
        )
        return cls.from_parts(ns_str, id_str)

    def to_dict(self) -> dict:
        """Return the portable dictionary form ``{"namespace": ..., "id": ...}``."""
        return {
            "namespace": str(self.namespace),
            "id": str(self.id),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create an object reference from its dictionary representation."""
        return cls.from_parts(data["namespace"], data["id"])
