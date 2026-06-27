from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .namespace import NamespaceDef
from .permissions import PermissionDef
from .relations import RelationDef
from .rules import (
    ExclusionRule,
    IntersectionRule,
    RewriteRule,
    TupleToUsersetRule,
    UnionRule,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

type SchemaDefinition = RelationDef | PermissionDef


@dataclass(slots=True)
class SchemaRegistry:
    """Owns the namespace set that defines an authorization model.

    Registry updates validate the whole schema before becoming visible, so
    callers can build or replace namespaces without exposing partial models.
    """

    _namespaces: dict[str, NamespaceDef] = field(default_factory=dict)

    def register(self, namespace: NamespaceDef) -> None:
        """Add or replace one namespace after full-registry validation."""

        validated = self._validated_namespace(namespace)
        candidate = dict(self._namespaces)
        candidate[validated.name] = validated
        self._validate_registry(candidate)
        self._namespaces = candidate

    def register_many(self, namespaces: Iterable[NamespaceDef]) -> None:
        """Register multiple namespaces as one atomic registry update."""

        pending: dict[str, NamespaceDef] = {}
        duplicates: set[str] = set()
        for ns in namespaces:
            validated = self._validated_namespace(ns)
            if validated.name in pending:
                duplicates.add(validated.name)
            pending[validated.name] = validated
        if duplicates:
            raise ValueError(f"Duplicate namespace names: {sorted(duplicates)}")

        candidate = dict(self._namespaces)
        candidate.update(pending)
        self._validate_registry(candidate)
        self._namespaces = candidate

    def update_namespace(self, namespace: NamespaceDef) -> None:
        """Update an existing namespace definition after full-registry validation."""

        name = namespace.name
        if name not in self._namespaces:
            raise ValueError(f"Unknown namespace: {name}")

        validated = self._validated_namespace(namespace)
        candidate = dict(self._namespaces)
        candidate[name] = validated
        self._validate_registry(candidate)
        self._namespaces = candidate

    def update_many(self, namespaces: Iterable[NamespaceDef]) -> None:
        """Batch update multiple existing namespaces atomically."""

        pending: dict[str, NamespaceDef] = {}
        duplicates: set[str] = set()
        for ns in namespaces:
            name = ns.name
            if name not in self._namespaces:
                raise ValueError(f"Unknown namespace: {name}")
            if name in pending:
                duplicates.add(name)
            pending[name] = self._validated_namespace(ns)
        if duplicates:
            raise ValueError(f"Duplicate namespace names: {sorted(duplicates)}")

        candidate = dict(self._namespaces)
        candidate.update(pending)
        self._validate_registry(candidate)
        self._namespaces = candidate

    def get_namespace(self, name: str) -> NamespaceDef:
        """Return a registered namespace.

        Raises:
            ValueError: If ``name`` is not registered.
        """

        try:
            return self._namespaces[name]
        except KeyError as exc:
            raise ValueError(f"Unknown namespace: {name}") from exc

    def get_definition(self, object_type: str, relation: str) -> SchemaDefinition:
        """Return the relation or permission definition named by a namespace edge."""

        ns = self.get_namespace(object_type)
        if relation in ns.relations:
            return ns.relations[relation]
        if relation in ns.permissions:
            return ns.permissions[relation]
        raise ValueError(
            f"Unknown relation or permission '{relation}' in namespace '{object_type}'"
        )

    def get_relation_definition(self, object_type: str, relation: str) -> dict:
        """Return a serialized relation or permission definition for engine use."""

        return self.get_definition(object_type, relation).to_dict()

    def list_namespaces(self) -> list[str]:
        """Return registered namespace names in deterministic order."""

        return sorted(self._namespaces.keys())

    def validate_all(self) -> None:
        """Validate namespace internals and cross-namespace schema references."""

        self._validate_registry(dict(self._namespaces))

    @staticmethod
    def _validated_namespace(namespace: NamespaceDef) -> NamespaceDef:
        return NamespaceDef.from_dict(namespace.to_dict())

    @classmethod
    def _validate_registry(cls, namespaces: Mapping[str, NamespaceDef]) -> None:
        for ns in namespaces.values():
            cls._validated_namespace(ns)
        cls._validate_allowed_subject_sets(namespaces)
        cls._validate_tuple_to_userset_targets(namespaces)

    @staticmethod
    def _validate_allowed_subject_sets(
        namespaces: Mapping[str, NamespaceDef],
    ) -> None:
        for ns in namespaces.values():
            for relation in ns.relations.values():
                for subject in relation.allowed_subjects:
                    if subject.relation is None:
                        continue
                    subject_namespace = subject.namespace.value
                    target = namespaces.get(subject_namespace)
                    if target is None:
                        raise ValueError(
                            f"relation '{ns.name}.{relation.name}': allowed subject "
                            f"namespace '{subject_namespace}' is not registered"
                        )
                    subject_relation = subject.relation.value
                    if subject_relation not in target.relations:
                        raise ValueError(
                            f"relation '{ns.name}.{relation.name}': allowed subject "
                            f"relation '{subject_namespace}#{subject_relation}' "
                            "is not a known relation"
                        )

    @classmethod
    def _validate_tuple_to_userset_targets(
        cls,
        namespaces: Mapping[str, NamespaceDef],
    ) -> None:
        for ns in namespaces.values():
            for owner, rewrite in cls._rewrite_owners(ns):
                for rule in cls._iter_rewrite_rules(rewrite):
                    if not isinstance(rule, TupleToUsersetRule):
                        continue

                    tuple_relation = ns.relations[rule.tuple_relation]
                    targets = tuple(
                        subject.namespace.value
                        for subject in tuple_relation.allowed_subjects
                        if subject.relation is None and not subject.wildcard
                    )
                    if not targets:
                        raise ValueError(
                            f"{owner}: tuple_to_userset.tuple_relation "
                            f"'{rule.tuple_relation}' has no object subject targets"
                        )

                    for target_namespace in targets:
                        target = namespaces.get(target_namespace)
                        if target is None:
                            raise ValueError(
                                f"{owner}: tuple_to_userset target namespace "
                                f"'{target_namespace}' is not registered"
                            )
                        if (
                            rule.computed_relation not in target.relations
                            and rule.computed_relation not in target.permissions
                        ):
                            raise ValueError(
                                f"{owner}: tuple_to_userset computed_relation "
                                f"'{target_namespace}#{rule.computed_relation}' "
                                "is not a known relation or permission"
                            )

    @staticmethod
    def _rewrite_owners(
        namespace: NamespaceDef,
    ) -> Iterable[tuple[str, RewriteRule]]:
        for relation in namespace.relations.values():
            if relation.rewrite is not None:
                yield f"relation '{namespace.name}.{relation.name}'", relation.rewrite
        for permission in namespace.permissions.values():
            yield f"permission '{namespace.name}.{permission.name}'", permission.rewrite

    @classmethod
    def _iter_rewrite_rules(cls, rewrite: RewriteRule) -> Iterable[RewriteRule]:
        yield rewrite
        if isinstance(rewrite, (UnionRule, IntersectionRule)):
            for child in rewrite.children:
                yield from cls._iter_rewrite_rules(child)
        elif isinstance(rewrite, ExclusionRule):
            yield from cls._iter_rewrite_rules(rewrite.base)
            yield from cls._iter_rewrite_rules(rewrite.subtract)

    @staticmethod
    def diff_namespaces(old: NamespaceDef, new: NamespaceDef) -> dict:
        """Describe relation, permission, and metadata changes between namespaces.

        The result is a shallow, JSON-friendly summary intended for migration
        previews and diagnostics; changed entries are detected by serialized
        definition comparison.
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
