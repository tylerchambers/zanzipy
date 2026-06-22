"""Rewrite rules for Zanzibar-style relation/permission definitions.

This module models the minimal set of rewrite rules used to compose
relations and permissions. Rules are exported to a portable dictionary
format (for JSON, etc.).

What this models
----------------
- A small algebra of rewrite nodes that mirrors Zanzibar:
  - Leaf nodes:
    - ThisRule: refers to direct tuple membership of the relation ("this").
      Only valid in relation rewrites.
    - ComputedUsersetRule: refers to another relation by name within the same
      namespace (e.g., "viewer").
    - TupleToUsersetRule: follows a relation from the object to a relation on
      the subject (e.g., "parent->viewer").
  - Operators:
    - UnionRule: any child grants access ("+").
    - IntersectionRule: all children required ("&").
    - ExclusionRule: base minus subtract ("-").

- Permissions are built purely from rewrite nodes; relations can optionally
  include rewrites and may also include ThisRule to incorporate direct tuples.

Examples
--------
- Direct stored relation (no rewrite):
    relation owner: user
  Represented as:
    DirectRule()

- Relation with direct tuples plus a subject-set:
    relation editor: user | group#member = this + group#member
  Represented as:
    UnionRule(
        children=(ThisRule(), ComputedUsersetRule("group#member"))
    )

- Permission that any owner or editor may access:
    permission can_view = owner + editor
  Represented as:
    UnionRule(
        children=(
            ComputedUsersetRule("owner"),
            ComputedUsersetRule("editor"),
        )
    )

- Permission that requires both member and not banned:
    permission can_comment = member - banned
  Represented as:
    ExclusionRule(
        base=ComputedUsersetRule("member"),
        subtract=ComputedUsersetRule("banned"),
    )

- Permission that requires both viewer and member:
    permission can_download = viewer & member
  Represented as:
    IntersectionRule(
        children=(
            ComputedUsersetRule("viewer"),
            ComputedUsersetRule("member"),
        )
    )
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from zanzipy.models import Relation as Rel
from zanzipy.schema.types import RewriteRuleType

# Type aliases for readability
RelationName = str


class RewriteRule(ABC):
    """Base class for relation rewrite rules"""

    @abstractmethod
    def to_dict(self) -> dict:
        """Serialize to portable JSON format"""
        pass

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RewriteRule:
        """Deserialize a rule from its dict form."""
        raw_type = data.get("type")
        try:
            rule_type = RewriteRuleType(raw_type)
        except ValueError as exc:
            raise ValueError(f"Unknown RewriteRule type: {raw_type}") from exc

        if rule_type is RewriteRuleType.THIS:
            return ThisRule()
        if rule_type is RewriteRuleType.DIRECT:
            return DirectRule()
        if rule_type is RewriteRuleType.COMPUTED_USERSET:
            return ComputedUsersetRule(relation=data["relation"])
        if rule_type is RewriteRuleType.TUPLE_TO_USERSET:
            return TupleToUsersetRule(
                tuple_relation=data["tuple_relation"],
                computed_relation=data["computed_relation"],
            )
        if rule_type is RewriteRuleType.UNION:
            return UnionRule(
                children=tuple(RewriteRule.from_dict(c) for c in data["children"])
            )
        if rule_type is RewriteRuleType.INTERSECTION:
            return IntersectionRule(
                children=tuple(RewriteRule.from_dict(c) for c in data["children"])
            )
        if rule_type is RewriteRuleType.EXCLUSION:
            return ExclusionRule(
                base=RewriteRule.from_dict(data["base"]),
                subtract=RewriteRule.from_dict(data["subtract"]),
            )
        raise AssertionError(f"Unhandled RewriteRule type: {rule_type}")


def _normalize_children(
    children: tuple[RewriteRule, ...],
    rule_name: str,
) -> tuple[RewriteRule, ...]:
    normalized = tuple(children)
    if not normalized:
        raise ValueError(f"{rule_name} requires at least one child")
    for child in normalized:
        if not isinstance(child, RewriteRule):
            raise TypeError(f"{rule_name} children must be rewrite rules")
    return normalized


def _require_rule(value: RewriteRule, operand_name: str) -> None:
    if not isinstance(value, RewriteRule):
        raise TypeError(f"{operand_name} must be a rewrite rule")


@dataclass(frozen=True, slots=True)
class ThisRule(RewriteRule):
    """Leaf that references the direct (stored) membership of the relation."""

    type: RewriteRuleType = field(default=RewriteRuleType.THIS, init=False)

    def to_dict(self) -> dict:
        return {"type": self.type}


@dataclass(frozen=True, slots=True)
class ComputedUsersetRule(RewriteRule):
    """Leaf that references another relation by name."""

    relation: RelationName

    type: RewriteRuleType = field(default=RewriteRuleType.COMPUTED_USERSET, init=False)

    def __post_init__(self) -> None:
        Rel(self.relation)

    def to_dict(self) -> dict:
        return {"type": self.type, "relation": self.relation}


@dataclass(frozen=True, slots=True)
class TupleToUsersetRule(RewriteRule):
    """Tuple-to-userset: follow a relation on the object
    to a relation on the subject."""

    tuple_relation: RelationName
    computed_relation: RelationName

    def __post_init__(self) -> None:
        Rel(self.tuple_relation)
        Rel(self.computed_relation)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "tuple_relation": self.tuple_relation,
            "computed_relation": self.computed_relation,
        }

    type: RewriteRuleType = field(default=RewriteRuleType.TUPLE_TO_USERSET, init=False)


@dataclass(frozen=True, slots=True)
class DirectRule(RewriteRule):
    """Direct relation assignment (stored tuples).

    Direct rules indicate that the relation is backed only by stored tuples
    (no rewrite).

    Example:
        relation owner: user  ->  DirectRule()
    """

    type: RewriteRuleType = field(default=RewriteRuleType.DIRECT, init=False)

    def to_dict(self) -> dict:
        return {"type": self.type}


@dataclass(frozen=True, slots=True)
class UnionRule(RewriteRule):
    """Union: access is granted if any of the children relations grant it.


    Example:
        permission can_view = owner + editor
        -> UnionRule(children=(
            ComputedUsersetRule("owner"),
            ComputedUsersetRule("editor"),
        ))
    """

    # Children are nested rewrite rules; operands must be typed nodes
    children: tuple[RewriteRule, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "children",
            _normalize_children(self.children, "union"),
        )

    def to_dict(self) -> dict:
        return {"type": self.type, "children": [c.to_dict() for c in self.children]}

    type: RewriteRuleType = field(default=RewriteRuleType.UNION, init=False)


@dataclass(frozen=True, slots=True)
class IntersectionRule(RewriteRule):
    """Intersection: access requires all children relations to grant it.


    Example:
        permission can_download = viewer & member
        -> IntersectionRule(children=(
            ComputedUsersetRule("viewer"),
            ComputedUsersetRule("member"),
        ))
    """

    # Children are nested rewrite rules; operands must be typed nodes
    children: tuple[RewriteRule, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "children",
            _normalize_children(self.children, "intersection"),
        )

    def to_dict(self) -> dict:
        return {"type": self.type, "children": [c.to_dict() for c in self.children]}

    type: RewriteRuleType = field(default=RewriteRuleType.INTERSECTION, init=False)


@dataclass(frozen=True, slots=True)
class ExclusionRule(RewriteRule):
    """Exclusion: grant from base but not from subtract.

    The expression is ``base - subtract``.

    Example:
        permission can_comment = member - banned
        -> ExclusionRule(
            base=ComputedUsersetRule("member"),
            subtract=ComputedUsersetRule("banned"),
        )
    """

    # Operands are nested rewrite rules; operands must be typed nodes
    base: RewriteRule
    subtract: RewriteRule

    def __post_init__(self) -> None:
        _require_rule(self.base, "base")
        _require_rule(self.subtract, "subtract")

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "base": self.base.to_dict(),
            "subtract": self.subtract.to_dict(),
        }

    type: RewriteRuleType = field(default=RewriteRuleType.EXCLUSION, init=False)
