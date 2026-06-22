from dataclasses import dataclass
from typing import TYPE_CHECKING

from zanzipy.models.relation import Relation as Rel
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

if TYPE_CHECKING:
    from collections.abc import Iterable


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
    """Encodes/decodes shorthand strings for subjects and rewrite operands.

    - Subject forms: "ns", "ns#rel", "ns:*"
    - Rule operand forms: "rel" or "a->b" (tuple-to-userset)

    This class is injectable for testability and extension.
    """

    def parse_subject(self, input_str: str) -> SubjectReference:
        if not isinstance(input_str, str) or not input_str:
            raise ValueError("subject must be a non-empty string")

        if input_str.endswith(":*"):
            ns = input_str[:-2]
            return SubjectReference(namespace=ns, wildcard=True)

        if "#" in input_str:
            ns, rel = input_str.split("#", 1)
            return SubjectReference(namespace=ns, relation=Rel(rel))

        return SubjectReference(namespace=input_str)

    def to_subjects(
        self,
        subjects: (Iterable[str] | Iterable[SubjectReference]) | None,
    ) -> tuple[SubjectReference, ...]:
        if subjects is None:
            return ()
        normalized: list[SubjectReference] = []
        for s in subjects:
            if isinstance(s, SubjectReference):
                normalized.append(s)
            elif isinstance(s, str):
                normalized.append(self.parse_subject(s))
            else:
                raise TypeError(
                    "subjects must be strings or SubjectReference instances"
                )
        return tuple(normalized)

    def name_to_rule(self, name: str) -> RewriteRule:
        if "->" in name:
            tuple_rel, comp_rel = name.split("->", 1)
            tuple_rel = tuple_rel.strip()
            comp_rel = comp_rel.strip()
            if not tuple_rel or not comp_rel:
                raise ValueError("tuple-to-userset shorthand must be 'a->b'")
            return TupleToUsersetRule(
                tuple_relation=tuple_rel, computed_relation=comp_rel
            )
        return ComputedUsersetRule(name)

    def names_to_children(self, names: Iterable[str]) -> tuple[RewriteRule, ...]:
        return tuple(self.name_to_rule(n) for n in names)


@dataclass
class _PendingRelation:
    name: str
    subjects: tuple[SubjectReference, ...]
    rewrite: RewriteRule | None


class NamespaceBuilder:
    """Fluent builder for creating namespaces with minimal boilerplate.

    Example:
        document = (
            NamespaceBuilder("document")
            .relation("owner", subjects=["user"])  # direct
            .relation("editor", subjects=["user", "group#member"])  # direct
            .permission("can_edit", union=["owner", "editor"])  # computed
            .permission("can_view", union=["can_edit", "viewer"])  # computed
            .build()
        )
    """

    def __init__(
        self,
        name: str,
        codec: DslCodec | None = None,
    ):
        self._name = name
        self._relations: dict[str, _PendingRelation] = {}
        self._permissions: dict[str, RewriteRule] = {}
        self._codec = codec or DslCodec()

    def relation(
        self,
        name: str,
        *,
        subjects: (Iterable[str] | Iterable[SubjectReference]) | None = None,
        direct: bool = True,
        rewrite: RewriteRule | None = None,
    ) -> NamespaceBuilder:
        """Add a relation.

        - If direct=True (default), implies ThisRule is allowed when composing
          relation rewrites later. A purely direct relation may be represented
          by leaving rewrite=None. The engine treats no rewrite as direct-only.
        - If you want to combine direct tuples with computed leaves, pass a
          rewrite that includes `ThisRule()` (e.g., UnionRule([ThisRule(), ...]) ).
        """

        subs = self._codec.to_subjects(subjects)
        if not subs:
            raise ValueError("relation requires at least one allowed subject type")

        if not direct and rewrite is None:
            raise ValueError("non-direct relation requires an explicit rewrite")

        self._relations[name] = _PendingRelation(
            name=name,
            subjects=subs,
            rewrite=rewrite,  # None implies pure direct relation
        )
        return self

    def permission(
        self,
        name: str,
        *,
        union: Iterable[str] | None = None,
        intersection: Iterable[str] | None = None,
        exclusion: tuple[str, str] | None = None,
    ) -> NamespaceBuilder:
        """Add a permission using a simple algebra.

        Exactly one of union, intersection, exclusion must be provided.
        Operands reference relation/permission names or tuple-to-userset
        shorthands like "parent->viewer".
        """

        provided = sum(x is not None for x in (union, intersection, exclusion))
        if provided != 1:
            raise ValueError(
                "Must specify exactly one of: union, intersection, exclusion"
            )

        if union is not None:
            rule = UnionRule(children=self._codec.names_to_children(union))
        elif intersection is not None:
            rule = IntersectionRule(
                children=self._codec.names_to_children(intersection)
            )
        else:
            if exclusion is None:
                raise ValueError(
                    "Must specify exactly one of: union, intersection, exclusion"
                )
            base, sub = exclusion
            rule = ExclusionRule(
                base=self._codec.name_to_rule(base),
                subtract=self._codec.name_to_rule(sub),
            )

        self._permissions[name] = rule
        return self

    # Expert API: allow explicit permission rewrite
    def permission_with_rewrite(
        self, name: str, rewrite: RewriteRule | None = None
    ) -> NamespaceBuilder:
        self._permissions[name] = rewrite
        return self

    def build(self) -> NamespaceDef:
        """Build the NamespaceDef (validation occurs inside NamespaceDef)."""

        relations: tuple[RelationDef, ...] = tuple(
            RelationDef(
                name=pr.name,
                allowed_subjects=pr.subjects,
                rewrite=pr.rewrite,
            )
            for pr in self._relations.values()
        )

        permissions: tuple[PermissionDef, ...] = tuple(
            PermissionDef(name=name, rewrite=rw)
            for name, rw in self._permissions.items()
        )

        return NamespaceDef(
            name=self._name,
            relations=relations,
            permissions=permissions,
        )


class SchemaBuilder:
    """Builder for creating complete schemas, with a shared default registry.

    By default, this builder uses a module-level shared registry so that all
    namespaces created in disparate places end up in the same registry without
    additional wiring. A custom registry can be provided to override this.
    """

    def __init__(self, registry: SchemaRegistry | None = None) -> None:
        self._registry: SchemaRegistry = registry or get_default_registry()
        self._pending: list[NamespaceDef] = []

    def add_namespace(self, namespace: NamespaceDef) -> SchemaBuilder:
        self._pending.append(namespace)
        return self

    def namespace(self, name: str) -> _InlineNS:
        """Start building a new namespace inline and auto-register on finish."""
        return _InlineNS(self, NamespaceBuilder(name))

    def build(self) -> SchemaRegistry:
        # Register staged namespaces first, then return the registry
        if self._pending:
            self._registry.register_many(self._pending)
            self._pending.clear()
        # Final pass validate
        self._registry.validate_all()
        return self._registry


class _InlineNS:
    """Fluent bridge for inline namespace construction.

    This small helper enables an ergonomic flow like:
        SchemaBuilder()\
            .namespace("doc")\
            .relation(...).permission(...)\
            .done()\
            .namespace("folder") ...

    Why it exists:
    - Keeps the fluent chain on `SchemaBuilder` without requiring callers to
      manually capture a `NamespaceBuilder` and then remember to register the
      result later.
    - `done()` builds the namespace and safely returns control to
      `SchemaBuilder`, reducing the chance of forgotten registration.
    - Encapsulates the inline-building concern so `SchemaBuilder` and
      `NamespaceBuilder` remain focused and modular.

    It is optional sugar; equivalent explicit usage is:
        ns = NamespaceBuilder("doc").relation(...).permission(...).build()
        SchemaBuilder().add_namespace(ns)
    """

    def __init__(self, owner: SchemaBuilder, builder: NamespaceBuilder) -> None:
        self._owner = owner
        self._builder = builder

    # Proxy subset of NamespaceBuilder API for fluent inline use
    def relation(
        self,
        name: str,
        *,
        subjects: (Iterable[str] | Iterable[SubjectReference]) | None = None,
        direct: bool = True,
        rewrite: RewriteRule | None = None,
    ) -> _InlineNS:
        self._builder.relation(
            name,
            subjects=subjects,
            direct=direct,
            rewrite=rewrite,
        )
        return self

    def permission(
        self,
        name: str,
        *,
        union: Iterable[str] | None = None,
        intersection: Iterable[str] | None = None,
        exclusion: tuple[str, str] | None = None,
    ) -> _InlineNS:
        self._builder.permission(
            name,
            union=union,
            intersection=intersection,
            exclusion=exclusion,
        )
        return self

    def done(self) -> SchemaBuilder:
        ns = self._builder.build()
        self._owner.add_namespace(ns)
        return self._owner
