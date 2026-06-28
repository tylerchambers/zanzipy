from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from .rules import DirectRule, RewriteRule

if TYPE_CHECKING:
    from collections.abc import Mapping

    from zanzipy.models import Subject
    from zanzipy.storage.cache.abstract.rules import CompiledRuleCache

    from .permissions import PermissionDef
    from .registry import SchemaRegistry
    from .relations import RelationDef


type RelationKey = tuple[str, str]
type UsersetReference = tuple[str, str]


@dataclass(frozen=True, slots=True)
class CompiledSubjectReference:
    """Allowed stored tuple subject shape for one relation edge."""

    namespace: str
    relation: str | None = None
    wildcard: bool = False

    def allows(
        self,
        *,
        namespace: str,
        entity_id: str,
        relation: str | None,
    ) -> bool:
        """Return whether a concrete stored tuple subject matches this shape."""
        if self.namespace != namespace:
            return False
        if self.wildcard:
            return relation is None and entity_id == "*"
        if entity_id == "*":
            return False
        if self.relation is None:
            return relation is None
        return relation is not None and self.relation == relation


@dataclass(frozen=True, slots=True)
class CompiledRelation:
    """Typed authorization metadata for one relation or permission edge."""

    rewrite: RewriteRule
    allowed_subjects: tuple[CompiledSubjectReference, ...] = ()
    tuple_to_userset_target_types: tuple[str, ...] = ()
    allowed_userset_refs: tuple[UsersetReference, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_subjects", tuple(self.allowed_subjects))
        object.__setattr__(
            self,
            "tuple_to_userset_target_types",
            tuple(self.tuple_to_userset_target_types),
        )
        object.__setattr__(
            self,
            "allowed_userset_refs",
            tuple(self.allowed_userset_refs),
        )


@dataclass(frozen=True, slots=True)
class CompiledAuthorizationModel:
    """Immutable, typed snapshot of a validated ``SchemaRegistry``.

    ``SchemaRegistry`` remains the validation authority. This model is the read
    path artifact: engines resolve pre-typed rewrite rules and lookup metadata
    from it without rehydrating schema dictionaries during traversal.
    """

    _namespaces: tuple[str, ...]
    _relations: Mapping[RelationKey, CompiledRelation]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_namespaces", tuple(self._namespaces))
        object.__setattr__(
            self,
            "_relations",
            MappingProxyType(dict(self._relations)),
        )

    @classmethod
    def from_schema(
        cls,
        schema: SchemaRegistry,
        *,
        compiled_rules_cache: CompiledRuleCache[RewriteRule] | None = None,
    ) -> CompiledAuthorizationModel:
        """Build a model snapshot from a validated schema registry.

        The optional rule cache is consulted during model construction only.
        Traversals use the compiled snapshot directly, so later registry changes
        require constructing a new model to affect authorization behavior.
        """

        schema.validate_all()

        relations: dict[RelationKey, CompiledRelation] = {}
        namespace_names = tuple(schema.list_namespaces())
        for namespace_name in namespace_names:
            namespace = schema.get_namespace(namespace_name)

            for relation in namespace.relations.values():
                key = (namespace_name, relation.name)
                rewrite = (
                    relation.rewrite if relation.rewrite is not None else DirectRule()
                )
                relations[key] = CompiledRelation(
                    rewrite=cls._cached_rewrite(
                        key,
                        rewrite,
                        compiled_rules_cache,
                    ),
                    allowed_subjects=cls._allowed_subjects(relation),
                    tuple_to_userset_target_types=cls._tuple_to_userset_targets(
                        relation
                    ),
                    allowed_userset_refs=cls._allowed_userset_refs(relation),
                )

            for permission in namespace.permissions.values():
                key = (namespace_name, permission.name)
                relations[key] = CompiledRelation(
                    rewrite=cls._cached_rewrite(
                        key,
                        cls._permission_rewrite(namespace_name, permission),
                        compiled_rules_cache,
                    )
                )

        return cls(namespace_names, relations)

    @property
    def namespaces(self) -> tuple[str, ...]:
        """Return namespace names captured by this model snapshot."""

        return self._namespaces

    def resolve(self, object_type: str, relation: str) -> RewriteRule:
        """Return the rewrite rule for a relation or permission edge."""

        return self._compiled_relation(object_type, relation).rewrite

    def tuple_to_userset_target_types(
        self,
        *,
        resource_type: str,
        tuple_relation: str,
    ) -> tuple[str, ...]:
        """Return object namespaces reachable through ``tuple_relation``."""

        return self._compiled_relation(
            resource_type,
            tuple_relation,
        ).tuple_to_userset_target_types

    def allowed_userset_refs(
        self,
        *,
        resource_type: str,
        relation: str,
    ) -> tuple[UsersetReference, ...]:
        """Return allowed subject-set references for a relation edge."""

        return self._compiled_relation(resource_type, relation).allowed_userset_refs

    def allows_subject(
        self,
        *,
        resource_type: str,
        relation: str,
        subject_type: str,
        subject_id: str,
        subject_relation: str | None,
    ) -> bool:
        """Return whether a stored tuple subject conforms to a relation schema."""
        return any(
            allowed.allows(
                namespace=subject_type,
                entity_id=subject_id,
                relation=subject_relation,
            )
            for allowed in self._compiled_relation(
                resource_type,
                relation,
            ).allowed_subjects
        )

    def allows_tuple_to_userset_subject(
        self,
        *,
        resource_type: str,
        tuple_relation: str,
        subject_type: str,
        subject_id: str,
        subject_relation: str | None,
    ) -> bool:
        """Return whether a tuple-to-userset edge may follow a stored subject."""
        return (
            subject_relation is None
            and subject_id != "*"
            and subject_type
            in self.tuple_to_userset_target_types(
                resource_type=resource_type,
                tuple_relation=tuple_relation,
            )
        )

    def allows_stored_subject(
        self,
        *,
        resource_type: str,
        relation: str,
        subject: Subject,
    ) -> bool:
        """Return whether a stored tuple subject matches relation metadata."""
        return self.allows_subject(
            resource_type=resource_type,
            relation=relation,
            subject_type=str(subject.namespace),
            subject_id=str(subject.id),
            subject_relation=self._subject_relation(subject),
        )

    def allows_tuple_to_userset_stored_subject(
        self,
        *,
        resource_type: str,
        tuple_relation: str,
        subject: Subject,
    ) -> bool:
        """Return whether tuple-to-userset traversal may follow this subject."""
        return self.allows_tuple_to_userset_subject(
            resource_type=resource_type,
            tuple_relation=tuple_relation,
            subject_type=str(subject.namespace),
            subject_id=str(subject.id),
            subject_relation=self._subject_relation(subject),
        )

    @staticmethod
    def _subject_relation(subject: Subject) -> str | None:
        return None if subject.relation is None else str(subject.relation)

    def _compiled_relation(self, object_type: str, relation: str) -> CompiledRelation:
        key = (object_type, relation)
        try:
            return self._relations[key]
        except KeyError as exc:
            if object_type not in self._namespaces:
                raise ValueError(f"Unknown namespace: {object_type}") from exc
            raise ValueError(
                f"Unknown relation or permission '{relation}' in namespace "
                f"'{object_type}'"
            ) from exc

    @staticmethod
    def _cached_rewrite(
        key: RelationKey,
        rewrite: RewriteRule,
        compiled_rules_cache: CompiledRuleCache[RewriteRule] | None,
    ) -> RewriteRule:
        if compiled_rules_cache is None:
            return rewrite

        namespace, name = key
        cached = compiled_rules_cache.get(namespace, name)
        if cached is not None and cached.to_dict() == rewrite.to_dict():
            return cached

        compiled_rules_cache.set(namespace, name, rewrite)
        return rewrite

    @staticmethod
    def _permission_rewrite(namespace: str, permission: PermissionDef) -> RewriteRule:
        rewrite = permission.rewrite
        if rewrite is None:
            raise ValueError(
                f"Permission has no rewrite: {namespace}:{permission.name}"
            )
        return rewrite

    @staticmethod
    def _tuple_to_userset_targets(relation: RelationDef) -> tuple[str, ...]:
        targets = {
            subject.namespace.value
            for subject in relation.allowed_subjects
            if subject.relation is None and not subject.wildcard
        }
        return tuple(sorted(targets))

    @staticmethod
    def _allowed_userset_refs(relation: RelationDef) -> tuple[UsersetReference, ...]:
        refs = {
            (subject.namespace.value, subject.relation.value)
            for subject in relation.allowed_subjects
            if subject.relation is not None
        }
        return tuple(sorted(refs))

    @staticmethod
    def _allowed_subjects(
        relation: RelationDef,
    ) -> tuple[CompiledSubjectReference, ...]:
        return tuple(
            CompiledSubjectReference(
                namespace=subject.namespace.value,
                relation=(None if subject.relation is None else subject.relation.value),
                wildcard=subject.wildcard,
            )
            for subject in relation.allowed_subjects
        )
