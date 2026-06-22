from dataclasses import dataclass
from typing import Self

from .id import EntityId
from .namespace import NamespaceId


@dataclass(frozen=True, slots=True)
class Obj:
    """Object value with namespace and id."""

    namespace: NamespaceId
    id: EntityId

    def __str__(self) -> str:
        return f"{self.namespace}:{self.id}"

    @classmethod
    def from_string(cls, object_string: str) -> Self:
        ns_str, id_str = object_string.split(":", 1)
        namespace = NamespaceId(ns_str)
        id = EntityId(id_str)
        return cls(namespace, id)

    def to_dict(self) -> dict:
        return {
            "namespace": str(self.namespace),
            "id": str(self.id),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        return cls(NamespaceId(data["namespace"]), EntityId(data["id"]))
