"""Shared structural dispatch for engine rewrite evaluation."""

from typing import TYPE_CHECKING, Concatenate, ParamSpec, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

from zanzipy.schema.rules import (
    ComputedUsersetRule,
    DirectRule,
    ExclusionRule,
    IntersectionRule,
    RewriteRule,
    ThisRule,
    TupleToUsersetRule,
    UnionRule,
)

P = ParamSpec("P")
R = TypeVar("R")


class RewriteRuleDispatcher:
    """Mixin that keeps structural rewrite dispatch on engine objects."""

    def _dispatch_rewrite_rule(
        self,
        rewrite: RewriteRule,
        direct: Callable[Concatenate[DirectRule, P], R],
        this: Callable[Concatenate[ThisRule, P], R],
        computed_userset: Callable[Concatenate[ComputedUsersetRule, P], R],
        tuple_to_userset: Callable[Concatenate[TupleToUsersetRule, P], R],
        union: Callable[Concatenate[UnionRule, P], R],
        intersection: Callable[Concatenate[IntersectionRule, P], R],
        exclusion: Callable[Concatenate[ExclusionRule, P], R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Dispatch a rewrite node to the handler for its concrete shape.

        This method owns only the structural rewrite-node dispatch shared by
        engine operations. Subclasses keep operation-specific traversal state,
        recursion, short-circuiting, and result construction in their handlers.
        """

        if isinstance(rewrite, DirectRule):
            return direct(rewrite, *args, **kwargs)
        if isinstance(rewrite, ThisRule):
            return this(rewrite, *args, **kwargs)
        if isinstance(rewrite, ComputedUsersetRule):
            return computed_userset(rewrite, *args, **kwargs)
        if isinstance(rewrite, TupleToUsersetRule):
            return tuple_to_userset(rewrite, *args, **kwargs)
        if isinstance(rewrite, UnionRule):
            return union(rewrite, *args, **kwargs)
        if isinstance(rewrite, IntersectionRule):
            return intersection(rewrite, *args, **kwargs)
        if isinstance(rewrite, ExclusionRule):
            return exclusion(rewrite, *args, **kwargs)

        raise TypeError(f"Unsupported rewrite rule type: {type(rewrite).__name__}")
