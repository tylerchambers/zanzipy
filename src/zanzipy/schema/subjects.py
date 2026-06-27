from typing import Self

from zanzipy.models import NamespaceId, Relation as Rel


class SubjectReference:
    """Allowed subject type for a relation definition.

    Supports direct namespaces (``user``), subject sets (``group#member``),
    and namespace wildcards (``user:*``).
    """

    __slots__ = ("_namespace", "_relation", "_wildcard")

    def __init__(
        self,
        *,
        namespace: NamespaceId | str,
        relation: Rel | str | None = None,
        wildcard: bool = False,
    ) -> None:
        """Normalize and validate a schema subject reference.

        Raises:
            TypeError: If inputs use unsupported types.
            ValueError: If a wildcard also specifies a relation.
        """
        # Normalize and validate inputs
        if isinstance(namespace, str):
            namespace = NamespaceId(namespace)
        elif not isinstance(namespace, NamespaceId):
            raise TypeError("namespace must be NamespaceId or str")

        if isinstance(relation, str):
            relation = Rel(relation)
        elif relation is not None and not isinstance(relation, Rel):
            raise TypeError("relation must be Relation, str, or None")

        if not isinstance(wildcard, bool):
            raise TypeError("wildcard must be bool")
        if wildcard and relation is not None:
            raise ValueError("wildcard and relation are mutually exclusive")

        self._namespace = namespace
        self._relation = relation
        self._wildcard = wildcard

    @property
    def namespace(self) -> NamespaceId:
        """Return the allowed subject namespace."""
        return self._namespace

    @property
    def relation(self) -> Rel | None:
        """Return the required subject-set relation, if any."""
        return self._relation

    @property
    def wildcard(self) -> bool:
        """Return whether this reference allows the namespace wildcard."""
        return self._wildcard

    def allows(
        self,
        *,
        namespace: str,
        entity_id: str,
        relation: str | None,
    ) -> bool:
        """Return whether a concrete tuple subject matches this reference."""
        if self.namespace.value != namespace:
            return False
        if self.wildcard:
            return relation is None and entity_id == "*"
        if entity_id == "*":
            return False
        if self.relation is None:
            return relation is None
        return relation is not None and self.relation.value == relation

    def to_dict(self) -> dict:
        """Serialize the subject reference to its schema dictionary."""
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
        """Deserialize a subject reference from its schema dictionary."""
        relation = data.get("relation")
        return cls(
            namespace=NamespaceId(data["namespace"]),
            relation=Rel(relation) if relation else None,
            wildcard=data.get("wildcard", False),
        )
