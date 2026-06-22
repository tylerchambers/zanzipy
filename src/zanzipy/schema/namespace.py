from types import MappingProxyType
from typing import TYPE_CHECKING, Self

from zanzipy.models import NamespaceId as Ns

from .permissions import PermissionDef
from .relations import RelationDef
from .rules import (
    ComputedUsersetRule,
    DirectRule,
    ExclusionRule,
    IntersectionRule,
    RewriteRule,
    ThisRule,
    TupleToUsersetRule,
    UnionRule,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class NamespaceDef:
    """Container for relations and permissions that belong to one namespace.

    The name follows identifier rules enforced by the `Namespace` value object.
    Relations and permissions are stored by name and validated for internal
    consistency:
    - No duplicate names across relations and permissions
    - Rewrite references must point to existing relations/permissions
    - `ThisRule` and `DirectRule` are only valid inside relation rewrites
    - `TupleToUsersetRule` must reference existing relation names
    """

    __slots__ = ("_permissions", "_relations", "description", "name")

    def __init__(
        self,
        *,
        name: str,
        relations: tuple[RelationDef, ...] | RelationDef | None = None,
        permissions: tuple[PermissionDef, ...] | PermissionDef | None = None,
        description: str | None = None,
    ) -> None:
        # Validate namespace identifier
        Ns(name)
        self.name = name
        self.description = description

        if isinstance(relations, RelationDef):
            relations = (relations,)
        if isinstance(permissions, PermissionDef):
            permissions = (permissions,)

        rel_tuple: tuple[RelationDef, ...] = tuple(relations or ())
        perm_tuple: tuple[PermissionDef, ...] = tuple(permissions or ())

        rel_dict = self._index_relations(rel_tuple)
        perm_dict = self._index_permissions(perm_tuple)

        relation_names = set(rel_dict.keys())
        permission_names = set(perm_dict.keys())

        overlap = relation_names & permission_names
        if overlap:
            raise ValueError(
                f"Names cannot be both relation and permission: {sorted(overlap)}"
            )

        # Validate rewrite graphs
        all_names = relation_names | permission_names
        for rel in rel_dict.values():
            if rel.rewrite is not None:
                self._validate_rewrite(
                    rewrite=rel.rewrite,
                    relation_names=relation_names,
                    all_names=all_names,
                    owner=f"relation '{rel.name}'",
                    allow_direct=True,
                )

        for perm in perm_dict.values():
            if perm.rewrite is None:
                raise ValueError(f"Permission '{perm.name}' must define a rewrite")
            self._validate_rewrite(
                rewrite=perm.rewrite,
                relation_names=relation_names,
                all_names=all_names,
                owner=f"permission '{perm.name}'",
                allow_direct=False,
            )

        self._relations = MappingProxyType(rel_dict)
        self._permissions = MappingProxyType(perm_dict)

    @property
    def relations(self) -> Mapping[str, RelationDef]:
        return self._relations

    @property
    def permissions(self) -> Mapping[str, PermissionDef]:
        return self._permissions

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "relations": {k: v.to_dict() for k, v in self.relations.items()},
            "permissions": {k: v.to_dict() for k, v in self.permissions.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        relations_raw = data.get("relations", {}) or {}
        permissions_raw = data.get("permissions", {}) or {}

        if not isinstance(relations_raw, dict):
            raise TypeError("'relations' must be a mapping of name to relation dict")
        if not isinstance(permissions_raw, dict):
            raise TypeError(
                "'permissions' must be a mapping of name to permission dict"
            )

        relation_defs = tuple(
            cls._relation_from_mapping(key, rel_dict)
            for key, rel_dict in relations_raw.items()
        )
        permission_defs = tuple(
            cls._permission_from_mapping(key, perm_dict)
            for key, perm_dict in permissions_raw.items()
        )

        return cls(
            name=data["name"],
            relations=relation_defs,
            permissions=permission_defs,
            description=data.get("description"),
        )

    @staticmethod
    def _index_relations(
        relations: tuple[RelationDef, ...],
    ) -> dict[str, RelationDef]:
        indexed: dict[str, RelationDef] = {}
        duplicates: set[str] = set()
        for relation in relations:
            if relation.name in indexed:
                duplicates.add(relation.name)
            indexed[relation.name] = relation
        if duplicates:
            raise ValueError(f"Duplicate relation names: {sorted(duplicates)}")
        return indexed

    @staticmethod
    def _index_permissions(
        permissions: tuple[PermissionDef, ...],
    ) -> dict[str, PermissionDef]:
        indexed: dict[str, PermissionDef] = {}
        duplicates: set[str] = set()
        for permission in permissions:
            if permission.name in indexed:
                duplicates.add(permission.name)
            indexed[permission.name] = permission
        if duplicates:
            raise ValueError(f"Duplicate permission names: {sorted(duplicates)}")
        return indexed

    @staticmethod
    def _relation_from_mapping(key: str, data: dict) -> RelationDef:
        relation = RelationDef.from_dict(data)
        if key != relation.name:
            raise ValueError(
                f"Relation mapping key '{key}' does not match definition name "
                f"'{relation.name}'"
            )
        return relation

    @staticmethod
    def _permission_from_mapping(key: str, data: dict) -> PermissionDef:
        permission = PermissionDef.from_dict(data)
        if key != permission.name:
            raise ValueError(
                f"Permission mapping key '{key}' does not match definition name "
                f"'{permission.name}'"
            )
        return permission

    @staticmethod
    def _validate_rewrite(
        *,
        rewrite: RewriteRule,
        relation_names: set[str],
        all_names: set[str],
        owner: str,
        allow_direct: bool,
    ) -> None:
        """Validate a rewrite tree against this namespace's local names.

        `TupleToUsersetRule.computed_relation` is intentionally not checked
        here: it is evaluated on the object reached through `tuple_relation`,
        so only `SchemaRegistry` has enough cross-namespace context to validate
        its target.
        """

        if isinstance(rewrite, (UnionRule, IntersectionRule)):
            for child in rewrite.children:
                NamespaceDef._validate_rewrite(
                    rewrite=child,
                    relation_names=relation_names,
                    all_names=all_names,
                    owner=owner,
                    allow_direct=allow_direct,
                )
            return

        if isinstance(rewrite, ExclusionRule):
            NamespaceDef._validate_rewrite(
                rewrite=rewrite.base,
                relation_names=relation_names,
                all_names=all_names,
                owner=owner,
                allow_direct=allow_direct,
            )
            NamespaceDef._validate_rewrite(
                rewrite=rewrite.subtract,
                relation_names=relation_names,
                all_names=all_names,
                owner=owner,
                allow_direct=allow_direct,
            )
            return

        if isinstance(rewrite, (ThisRule, DirectRule)):
            if not allow_direct:
                raise ValueError(
                    f"{owner}: 'this' and 'direct' are not valid in permissions"
                )
            return

        if isinstance(rewrite, ComputedUsersetRule):
            if rewrite.relation not in all_names:
                raise ValueError(
                    f"{owner}: computed userset references unknown name "
                    f"'{rewrite.relation}'"
                )
            return

        if isinstance(rewrite, TupleToUsersetRule):
            if rewrite.tuple_relation not in relation_names:
                raise ValueError(
                    f"{owner}: tuple_to_userset.tuple_relation "
                    f"'{rewrite.tuple_relation}' is not a known relation"
                )
            return

        raise ValueError(
            f"{owner}: unknown rewrite node type: {type(rewrite).__name__}"
        )
