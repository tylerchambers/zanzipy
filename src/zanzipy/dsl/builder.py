from collections.abc import Iterable, Sequence
from typing import Self

from zanzipy.models import Relation as Rel
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import (
    ComputedUsersetRule,
    ExclusionRule,
    IntersectionRule,
    RewriteRule,
    TupleToUsersetRule,
    UnionRule,
)
from zanzipy.schema.subjects import SubjectReference

type SubjectInput = str | SubjectReference | Iterable[str | SubjectReference]
type RuleInput = str | Iterable[str]
type ExclusionInput = Sequence[str]

# A process-wide default registry to satisfy the requirement that
# all namespace definitions go into the same registry by default.
# Users can still pass their own registry to SchemaBuilder if needed.
_DEFAULT_REGISTRY = SchemaRegistry()


def get_default_registry() -> SchemaRegistry:
    """Return the module-level default `SchemaRegistry`.

    This allows multiple builder instances to collaboratively register into
    the same process-wide registry with zero configuration.
    """

    return _DEFAULT_REGISTRY


class DslCodec:
    """Parse DSL shorthand strings into schema value objects.

    Subject forms: ``"ns"``, ``"ns#rel"``, ``"ns:*"``.
    Rule operand forms: ``"rel"`` or ``"tuple_rel->computed_rel"``.
    """

    @staticmethod
    def parse_subject(input_str: str) -> SubjectReference:
        """Parse one subject shorthand into a `SubjectReference`.

        Accepted forms are ``"namespace"``, ``"namespace#relation"``, and
        ``"namespace:*"`` for wildcard subjects.
        """
        if not isinstance(input_str, str) or not input_str.strip():
            raise ValueError("subject must be a non-empty string")

        value = input_str.strip()
        if value.endswith(":*"):
            return SubjectReference(namespace=value[:-2], wildcard=True)

        if "#" in value:
            namespace, relation = value.split("#", 1)
            return SubjectReference(namespace=namespace, relation=Rel(relation))

        return SubjectReference(namespace=value)

    def to_subjects(
        self,
        subjects: SubjectInput | None,
    ) -> tuple[SubjectReference, ...]:
        """Normalize relation subject declarations.

        Accepts one string/reference or an iterable of them. ``None`` returns
        an empty tuple so callers can decide whether subjects are required.
        """
        if subjects is None:
            return ()

        values = (
            (subjects,)
            if isinstance(subjects, (str, SubjectReference))
            else tuple(subjects)
        )
        normalized: list[SubjectReference] = []
        for subject in values:
            if isinstance(subject, SubjectReference):
                normalized.append(subject)
            elif isinstance(subject, str):
                normalized.append(self.parse_subject(subject))
            else:
                raise TypeError(
                    "subjects must be strings or SubjectReference instances"
                )
        return tuple(normalized)

    @staticmethod
    def name_to_rule(name: str) -> RewriteRule:
        """Convert one permission operand shorthand into a rewrite rule.

        Plain names become computed-userset references. ``"a->b"`` becomes a
        tuple-to-userset rule from relation ``a`` to computed relation ``b``.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("rule operand must be a non-empty string")

        value = name.strip()
        if "->" in value:
            tuple_relation, computed_relation = value.split("->", 1)
            tuple_relation = tuple_relation.strip()
            computed_relation = computed_relation.strip()
            if not tuple_relation or not computed_relation:
                raise ValueError("tuple-to-userset shorthand must be 'a->b'")
            return TupleToUsersetRule(
                tuple_relation=tuple_relation,
                computed_relation=computed_relation,
            )
        return ComputedUsersetRule(value)

    def names_to_children(self, names: RuleInput) -> tuple[RewriteRule, ...]:
        """Convert one or more operand shorthands into rewrite-rule children."""
        values = (names,) if isinstance(names, str) else tuple(names)
        return tuple(self.name_to_rule(name) for name in values)


class NamespaceBuilder:
    """Fluent builder for one namespace.

    A relation without a rewrite is direct-only. A relation with a rewrite uses
    that rewrite exactly; include ``ThisRule()`` in the rewrite when direct
    tuples should participate in a composite relation.
    """

    def __init__(
        self,
        name: str,
        codec: DslCodec | None = None,
        *,
        owner: SchemaBuilder | None = None,
    ) -> None:
        """Create a namespace builder.

        ``name`` is validated when the namespace is built. ``codec`` lets
        advanced callers customize shorthand parsing; ``owner`` is used by
        `SchemaBuilder.namespace()` for inline fluent construction.
        """
        self._name = name
        self._relations: dict[str, RelationDef] = {}
        self._permissions: dict[str, RewriteRule] = {}
        self._codec = codec or DslCodec()
        self._owner = owner

    def relation(
        self,
        name: str,
        *,
        subjects: SubjectInput | None = None,
        rewrite: RewriteRule | None = None,
    ) -> Self:
        """Add or replace a relation definition.

        ``subjects`` declares allowed subject types as strings or
        `SubjectReference` instances. Leave ``rewrite`` unset for a direct
        stored relation; pass a rewrite tree for computed relation semantics.
        """

        allowed_subjects = self._codec.to_subjects(subjects)
        if not allowed_subjects:
            raise ValueError("relation requires at least one allowed subject type")

        self._relations[name] = RelationDef(
            name=name,
            allowed_subjects=allowed_subjects,
            rewrite=rewrite,
        )
        return self

    def permission(
        self,
        name: str,
        *,
        union: RuleInput | None = None,
        intersection: RuleInput | None = None,
        exclusion: ExclusionInput | None = None,
    ) -> Self:
        """Add or replace a permission built from one shorthand operator.

        Use exactly one of ``union``, ``intersection``, or ``exclusion``.
        Operands are relation/permission names or ``"tuple->relation"``
        tuple-to-userset shorthands.
        """

        provided = sum(value is not None for value in (union, intersection, exclusion))
        if provided != 1:
            raise ValueError(
                "Must specify exactly one of: union, intersection, exclusion"
            )

        if union is not None:
            rewrite = UnionRule(children=self._required_children("union", union))
        elif intersection is not None:
            rewrite = IntersectionRule(
                children=self._required_children("intersection", intersection)
            )
        else:
            rewrite = self._exclusion_rule(exclusion)

        self._permissions[name] = rewrite
        return self

    def permission_with_rewrite(self, name: str, rewrite: RewriteRule) -> Self:
        """Add or replace a permission using an explicit rewrite tree.

        Use this when the simple shorthand operators are not expressive enough
        for the permission shape you need.
        """

        self._permissions[name] = rewrite
        return self

    def build(self) -> NamespaceDef:
        """Build and validate a `NamespaceDef` from the staged definitions."""

        permissions = tuple(
            PermissionDef(name=name, rewrite=rewrite)
            for name, rewrite in self._permissions.items()
        )
        return NamespaceDef(
            name=self._name,
            relations=tuple(self._relations.values()),
            permissions=permissions,
        )

    def done(self) -> SchemaBuilder:
        """Stage this inline namespace and return to its `SchemaBuilder`.

        Only builders created by `SchemaBuilder.namespace()` have an owner.
        Standalone namespace builders should call `build()` directly.
        """

        if self._owner is None:
            raise ValueError("done() is only available for inline schema namespaces")
        self._owner.add_namespace(self.build())
        return self._owner

    def _required_children(
        self,
        operator: str,
        operands: RuleInput,
    ) -> tuple[RewriteRule, ...]:
        children = self._codec.names_to_children(operands)
        if not children:
            raise ValueError(f"{operator} requires at least one operand")
        return children

    def _exclusion_rule(self, operands: ExclusionInput | None) -> ExclusionRule:
        if operands is None:
            raise ValueError("exclusion requires exactly two operands")
        if isinstance(operands, str) or len(operands) != 2:
            raise ValueError("exclusion requires exactly two operands")

        base, subtract = operands
        return ExclusionRule(
            base=self._codec.name_to_rule(base),
            subtract=self._codec.name_to_rule(subtract),
        )


class SchemaBuilder:
    """Builder for complete schemas backed by a shared default registry."""

    def __init__(
        self,
        registry: SchemaRegistry | None = None,
        codec: DslCodec | None = None,
    ) -> None:
        """Create a schema builder.

        Without ``registry``, builders share the process-wide default registry.
        Pass an explicit registry to isolate schemas in tests or applications.
        """
        self._registry = registry if registry is not None else get_default_registry()
        self._codec = codec or DslCodec()
        self._pending: list[NamespaceDef] = []

    def add_namespace(self, namespace: NamespaceDef) -> Self:
        """Stage a completed namespace for registration during `build()`."""
        self._pending.append(namespace)
        return self

    def namespace(self, name: str) -> NamespaceBuilder:
        """Start an inline namespace builder.

        Chain relation and permission calls on the returned `NamespaceBuilder`,
        then call `NamespaceBuilder.done()` to stage it on this schema builder.
        """

        return NamespaceBuilder(name, codec=self._codec, owner=self)

    def build(self) -> SchemaRegistry:
        """Register staged namespaces, validate the registry, and return it."""
        if self._pending:
            self._registry.register_many(self._pending)
            self._pending.clear()
        self._registry.validate_all()
        return self._registry
