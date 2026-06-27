import pytest

from zanzipy.engine.rewrite_dispatch import RewriteRuleDispatcher
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


class UnknownRule(RewriteRule):
    def to_dict(self) -> dict:
        return {"type": "unknown"}


class ConcreteRewriteRuleDispatcher(RewriteRuleDispatcher):
    pass


def _direct(_rewrite: DirectRule, *, marker: str) -> str:
    return f"direct:{marker}"


def _this(_rewrite: ThisRule, *, marker: str) -> str:
    return f"this:{marker}"


def _computed_userset(_rewrite: ComputedUsersetRule, *, marker: str) -> str:
    return f"computed_userset:{marker}"


def _tuple_to_userset(_rewrite: TupleToUsersetRule, *, marker: str) -> str:
    return f"tuple_to_userset:{marker}"


def _union(_rewrite: UnionRule, *, marker: str) -> str:
    return f"union:{marker}"


def _intersection(_rewrite: IntersectionRule, *, marker: str) -> str:
    return f"intersection:{marker}"


def _exclusion(_rewrite: ExclusionRule, *, marker: str) -> str:
    return f"exclusion:{marker}"


@pytest.mark.parametrize(
    ("rewrite", "expected"),
    [
        (DirectRule(), "direct:state"),
        (ThisRule(), "this:state"),
        (ComputedUsersetRule("viewer"), "computed_userset:state"),
        (TupleToUsersetRule("parent", "viewer"), "tuple_to_userset:state"),
        (UnionRule((ThisRule(),)), "union:state"),
        (IntersectionRule((ThisRule(),)), "intersection:state"),
        (
            ExclusionRule(ThisRule(), ComputedUsersetRule("banned")),
            "exclusion:state",
        ),
    ],
)
def test_rewrite_rule_dispatcher_calls_matching_handler(
    rewrite: RewriteRule,
    expected: str,
) -> None:
    assert (
        ConcreteRewriteRuleDispatcher()._dispatch_rewrite_rule(
            rewrite,
            direct=_direct,
            this=_this,
            computed_userset=_computed_userset,
            tuple_to_userset=_tuple_to_userset,
            union=_union,
            intersection=_intersection,
            exclusion=_exclusion,
            marker="state",
        )
        == expected
    )


def test_rewrite_rule_dispatcher_rejects_unknown_subclass() -> None:
    with pytest.raises(TypeError, match="Unsupported rewrite rule type: UnknownRule"):
        ConcreteRewriteRuleDispatcher()._dispatch_rewrite_rule(
            UnknownRule(),
            direct=_direct,
            this=_this,
            computed_userset=_computed_userset,
            tuple_to_userset=_tuple_to_userset,
            union=_union,
            intersection=_intersection,
            exclusion=_exclusion,
            marker="state",
        )
