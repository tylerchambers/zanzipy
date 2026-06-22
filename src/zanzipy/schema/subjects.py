from typing import Self

from zanzipy.models.namespace import NamespaceId
from zanzipy.models.relation import Relation as Rel


class SubjectReference:
    """Represents an allowed subject type for a relation.

    Forms supported (SpiceDB compatible):
    - "ns" (e.g., user)
    - "ns#rel" (e.g., group#member)
    - "ns:*" (namespace wildcard)
    """

    __slots__ = ("_namespace", "_relation", "_wildcard")

    def __init__(
        self,
        *,
        namespace: NamespaceId | str,
        relation: Rel | None = None,
        wildcard: bool = False,
    ) -> None:
        # Normalize and validate inputs
        if isinstance(namespace, str):
            namespace = NamespaceId(namespace)
        elif not isinstance(namespace, NamespaceId):
            raise TypeError("namespace must be NamespaceId or str")

        if wildcard and relation is not None:
            raise ValueError("wildcard and relation are mutually exclusive")

        self._namespace = namespace
        self._relation = relation
        self._wildcard = bool(wildcard)

    @property
    def namespace(self) -> NamespaceId:
        return self._namespace

    @property
    def relation(self) -> Rel | None:
        return self._relation

    @property
    def wildcard(self) -> bool:
        return self._wildcard

    def to_dict(self) -> dict:
        return {
            "namespace": self.namespace.value,
            "relation": (self.relation.value if self.relation else None),
            "wildcard": self.wildcard,
        }

    def __eq__(self, other: object) -> bool:
        if self is other:
            return True
        if not isinstance(other, SubjectReference):
            return False
        return (
            self._namespace == other._namespace
            and self._relation == other._relation
            and self._wildcard == other._wildcard
        )

    def __hash__(self) -> int:
        return hash((self._namespace, self._relation, self._wildcard))

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        relation = data.get("relation")
        return cls(
            namespace=NamespaceId(data["namespace"]),
            relation=Rel(relation) if relation else None,
            wildcard=bool(data.get("wildcard")),
        )
