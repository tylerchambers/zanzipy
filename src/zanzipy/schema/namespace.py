from typing import Self

from zanzipy.models.namespace import NamespaceId as Ns

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

        # Build dictionaries with last-one-wins for duplicate names
        rel_dict: dict[str, RelationDef] = {}
        for rel in rel_tuple:
            rel_dict[rel.name] = rel
        perm_dict: dict[str, PermissionDef] = {}
        for perm in perm_tuple:
            perm_dict[perm.name] = perm

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
                self._validate_relation_rewrite(
                    rewrite=rel.rewrite,
                    relation_names=relation_names,
                    all_names=all_names,
                    owner=f"relation '{rel.name}'",
                )

        for perm in perm_dict.values():
            if perm.rewrite is None:
                raise ValueError(f"Permission '{perm.name}' must define a rewrite")
            self._validate_permission_rewrite(
                rewrite=perm.rewrite,
                relation_names=relation_names,
                all_names=all_names,
                owner=f"permission '{perm.name}'",
            )

        self._relations = rel_dict
        self._permissions = perm_dict

    @property
    def relations(self) -> dict[str, RelationDef]:
        return self._relations

    @property
    def permissions(self) -> dict[str, PermissionDef]:
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
            RelationDef.from_dict(rel_dict) for rel_dict in relations_raw.values()
        )
        permission_defs = tuple(
            PermissionDef.from_dict(perm_dict) for perm_dict in permissions_raw.values()
        )

        return cls(
            name=data["name"],
            relations=relation_defs,
            permissions=permission_defs,
            description=data.get("description"),
        )

    @staticmethod
    def _validate_relation_rewrite(
        *,
        rewrite: RewriteRule,
        relation_names: set[str],
        all_names: set[str],
        owner: str,
    ) -> None:
        """Validate a rewrite attached to a relation.

        Constraints:
        - `ThisRule` and `DirectRule` are allowed in relations
        - `ComputedUsersetRule` must reference an existing relation/permission
        - `TupleToUsersetRule` must reference existing relations by name
        """

        if isinstance(rewrite, (UnionRule, IntersectionRule)):
            for child in rewrite.children:
                NamespaceDef._validate_relation_rewrite(
                    rewrite=child,
                    relation_names=relation_names,
                    all_names=all_names,
                    owner=owner,
                )
            return

        if isinstance(rewrite, ExclusionRule):
            NamespaceDef._validate_relation_rewrite(
                rewrite=rewrite.base,
                relation_names=relation_names,
                all_names=all_names,
                owner=owner,
            )
            NamespaceDef._validate_relation_rewrite(
                rewrite=rewrite.subtract,
                relation_names=relation_names,
                all_names=all_names,
                owner=owner,
            )
            return

        if isinstance(rewrite, ThisRule):
            return

        if isinstance(rewrite, DirectRule):
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
            if rewrite.computed_relation not in relation_names:
                raise ValueError(
                    f"{owner}: tuple_to_userset.computed_relation "
                    f"'{rewrite.computed_relation}' is not a known relation"
                )
            return

        # If we reach here the node type is unknown at runtime
        raise ValueError(
            f"{owner}: unknown rewrite node type: {type(rewrite).__name__}"
        )

    @staticmethod
    def _validate_permission_rewrite(
        *,
        rewrite: RewriteRule,
        relation_names: set[str],
        all_names: set[str],
        owner: str,
    ) -> None:
        """Validate a rewrite attached to a permission.

        Constraints:
        - `ThisRule` and `DirectRule` are NOT valid in permissions
        - `ComputedUsersetRule` must reference an existing relation/permission
        - `TupleToUsersetRule` must reference existing relations by name
        """

        if isinstance(rewrite, (UnionRule, IntersectionRule)):
            for child in rewrite.children:
                NamespaceDef._validate_permission_rewrite(
                    rewrite=child,
                    relation_names=relation_names,
                    all_names=all_names,
                    owner=owner,
                )
            return

        if isinstance(rewrite, ExclusionRule):
            NamespaceDef._validate_permission_rewrite(
                rewrite=rewrite.base,
                relation_names=relation_names,
                all_names=all_names,
                owner=owner,
            )
            NamespaceDef._validate_permission_rewrite(
                rewrite=rewrite.subtract,
                relation_names=relation_names,
                all_names=all_names,
                owner=owner,
            )
            return

        if isinstance(rewrite, (ThisRule, DirectRule)):
            raise ValueError(
                f"{owner}: 'this' and 'direct' are not valid in permissions"
            )

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
            if rewrite.computed_relation not in relation_names:
                raise ValueError(
                    f"{owner}: tuple_to_userset.computed_relation "
                    f"'{rewrite.computed_relation}' is not a known relation"
                )
            return

        # If we reach here the node type is unknown at runtime
        raise ValueError(
            f"{owner}: unknown rewrite node type: {type(rewrite).__name__}"
        )
