from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .namespace import NamespaceDef

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(slots=True)
class SchemaRegistry:
    """Central registry for all namespace definitions.

    Manages the authorization model for the entire system.
    """

    _namespaces: dict[str, NamespaceDef] = field(default_factory=dict)

    def register(self, namespace: NamespaceDef) -> None:
        """Register a namespace definition after validation.

        Validation: construction of NamespaceDef already validates its internals.
        For additional safety, we re-run __post_init__ by re-instantiating via
        from_dict to ensure no mutations bypassed it (NamespaceDef is frozen,
        so this is mostly defensive).
        """

        validated = NamespaceDef.from_dict(namespace.to_dict())
        self._namespaces[validated.name] = validated

    def register_many(self, namespaces: Iterable[NamespaceDef]) -> None:
        """Register multiple namespaces."""

        for ns in namespaces:
            self.register(ns)

    def update_namespace(self, namespace: NamespaceDef) -> None:
        """Update an existing namespace definition.

        Raises ValueError if the namespace name is not already registered.
        The replacement is validated via a round-trip.
        """

        name = namespace.name
        if name not in self._namespaces:
            raise ValueError(f"Unknown namespace: {name}")
        validated = NamespaceDef.from_dict(namespace.to_dict())
        self._namespaces[name] = validated

    def update_many(self, namespaces: Iterable[NamespaceDef]) -> None:
        """Batch update multiple existing namespaces atomically.

        - Validates all inputs first
        - All names must already exist
        - Applies all replacements as a single mapping update
        """

        pending: dict[str, NamespaceDef] = {}
        for ns in namespaces:
            name = ns.name
            if name not in self._namespaces:
                raise ValueError(f"Unknown namespace: {name}")
            pending[name] = NamespaceDef.from_dict(ns.to_dict())

        for name, validated in pending.items():
            self._namespaces[name] = validated

    def get_namespace(self, name: str) -> NamespaceDef:
        """Get a namespace by name.

        Raises ValueError if not found.
        """

        try:
            return self._namespaces[name]
        except KeyError as exc:
            raise ValueError(f"Unknown namespace: {name}") from exc

    def get_relation_definition(self, object_type: str, relation: str) -> dict:
        """Get a relation or permission definition by name from a namespace.

        Returns its dict form for portability; callers can access the underlying
        object by reading the registry directly if needed.
        """

        ns = self.get_namespace(object_type)
        if relation in ns.relations:
            return ns.relations[relation].to_dict()
        if relation in ns.permissions:
            return ns.permissions[relation].to_dict()
        raise ValueError(
            f"Unknown relation or permission '{relation}' in namespace '{object_type}'"
        )

    def list_namespaces(self) -> list[str]:
        """List all registered namespace names (sorted)."""

        return sorted(self._namespaces.keys())

    def validate_all(self) -> None:
        """Validate all registered namespaces by re-instantiating them.

        Ensures internal graphs are consistent across the registry state.
        """

        for ns in list(self._namespaces.values()):
            NamespaceDef.from_dict(ns.to_dict())

    @staticmethod
    def diff_namespaces(old: NamespaceDef, new: NamespaceDef) -> dict:
        """Compute a shallow diff between two NamespaceDef instances.

        Returns a dictionary with added/removed/changed names for relations and
        permissions, plus top-level name/description changes. 'Changed' is based
        on per-entry dict comparison.
        """

        old_rel = old.relations
        new_rel = new.relations
        old_perm = old.permissions
        new_perm = new.permissions

        rel_added = sorted(n for n in new_rel if n not in old_rel)
        rel_removed = sorted(n for n in old_rel if n not in new_rel)
        rel_common = (n for n in new_rel if n in old_rel)
        rel_changed = sorted(
            n for n in rel_common if new_rel[n].to_dict() != old_rel[n].to_dict()
        )

        perm_added = sorted(n for n in new_perm if n not in old_perm)
        perm_removed = sorted(n for n in old_perm if n not in new_perm)
        perm_common = (n for n in new_perm if n in old_perm)
        perm_changed = sorted(
            n for n in perm_common if new_perm[n].to_dict() != old_perm[n].to_dict()
        )

        top_level = {
            "name_changed": old.name != new.name,
            "description_changed": old.description != new.description,
        }

        return {
            "top_level": top_level,
            "relations": {
                "added": rel_added,
                "removed": rel_removed,
                "changed": rel_changed,
            },
            "permissions": {
                "added": perm_added,
                "removed": perm_removed,
                "changed": perm_changed,
            },
        }
