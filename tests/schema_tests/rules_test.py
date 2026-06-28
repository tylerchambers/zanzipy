import pytest

from zanzipy.models.errors import IdentifierValidationError
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


class TestDirectRule:
    def test_to_dict_and_spicedb(self) -> None:
        rule = DirectRule()
        assert rule.to_dict() == {"type": "direct"}


@pytest.mark.parametrize(
    ("children", "expected_dict", "expected_spicedb"),
    [
        (
            (ComputedUsersetRule("viewer"),),
            {
                "type": "union",
                "children": [{"type": "computed_userset", "relation": "viewer"}],
            },
            "viewer",
        ),
        (
            (ComputedUsersetRule("viewer"), ComputedUsersetRule("editor")),
            {
                "type": "union",
                "children": [
                    {"type": "computed_userset", "relation": "viewer"},
                    {"type": "computed_userset", "relation": "editor"},
                ],
            },
            "viewer + editor",
        ),
    ],
)
class TestUnionRule:
    def test_union(self, children, expected_dict, expected_spicedb) -> None:
        rule = UnionRule(children=children)
        assert rule.to_dict() == expected_dict

    def test_empty_union_rejected(
        self, children, expected_dict, expected_spicedb
    ) -> None:
        with pytest.raises(ValueError, match="union requires at least one child"):
            UnionRule(children=())


@pytest.mark.parametrize(
    ("children", "expected_dict", "expected_spicedb"),
    [
        (
            (ComputedUsersetRule("viewer"), ComputedUsersetRule("member")),
            {
                "type": "intersection",
                "children": [
                    {"type": "computed_userset", "relation": "viewer"},
                    {"type": "computed_userset", "relation": "member"},
                ],
            },
            "viewer & member",
        ),
    ],
)
class TestIntersectionRule:
    def test_intersection(self, children, expected_dict, expected_spicedb) -> None:
        rule = IntersectionRule(children=children)
        assert rule.to_dict() == expected_dict

    def test_empty_intersection_rejected(
        self, children, expected_dict, expected_spicedb
    ) -> None:
        with pytest.raises(
            ValueError, match="intersection requires at least one child"
        ):
            IntersectionRule(children=())


@pytest.mark.parametrize(
    ("base", "subtract", "expected_dict", "expected_spicedb"),
    [
        (
            ComputedUsersetRule("member"),
            ComputedUsersetRule("banned"),
            {
                "type": "exclusion",
                "base": {"type": "computed_userset", "relation": "member"},
                "subtract": {"type": "computed_userset", "relation": "banned"},
            },
            "member - banned",
        ),
    ],
)
class TestExclusionRule:
    def test_exclusion(self, base, subtract, expected_dict, expected_spicedb) -> None:
        rule = ExclusionRule(base=base, subtract=subtract)
        assert rule.to_dict() == expected_dict

    def test_rejects_invalid_relation_names(
        self, base, subtract, expected_dict, expected_spicedb
    ) -> None:
        with pytest.raises(IdentifierValidationError, match="identifier"):
            ComputedUsersetRule("")
        with pytest.raises(IdentifierValidationError, match="identifier"):
            TupleToUsersetRule(tuple_relation="parent", computed_relation="")


class TestRewriteRuleFromDict:
    def test_from_dict_this(self) -> None:
        d = {"type": "this"}
        rule = RewriteRule.from_dict(d)
        assert isinstance(rule, ThisRule)
        assert rule.to_dict() == d

    def test_from_dict_direct(self) -> None:
        d = {"type": "direct"}
        rule = RewriteRule.from_dict(d)
        assert isinstance(rule, DirectRule)
        assert rule.to_dict() == d

    def test_from_dict_computed_userset(self) -> None:
        d = {"type": "computed_userset", "relation": "viewer"}
        rule = RewriteRule.from_dict(d)
        assert isinstance(rule, ComputedUsersetRule)
        assert rule.to_dict() == d

    def test_from_dict_tuple_to_userset(self) -> None:
        d = {
            "type": "tuple_to_userset",
            "tuple_relation": "parent",
            "computed_relation": "member",
        }
        rule = RewriteRule.from_dict(d)
        assert isinstance(rule, TupleToUsersetRule)
        assert rule.to_dict() == d

    def test_from_dict_empty_union_raises(self) -> None:
        with pytest.raises(ValueError, match="union requires at least one child"):
            RewriteRule.from_dict({"type": "union", "children": []})

    def test_from_dict_union_with_children(self) -> None:
        d = {
            "type": "union",
            "children": [
                {"type": "computed_userset", "relation": "viewer"},
                {"type": "computed_userset", "relation": "editor"},
            ],
        }
        rule = RewriteRule.from_dict(d)
        assert isinstance(rule, UnionRule)
        assert rule.to_dict() == d

    def test_from_dict_empty_intersection_raises(self) -> None:
        with pytest.raises(
            ValueError, match="intersection requires at least one child"
        ):
            RewriteRule.from_dict({"type": "intersection", "children": []})

    def test_from_dict_intersection_with_children(self) -> None:
        d = {
            "type": "intersection",
            "children": [
                {"type": "computed_userset", "relation": "viewer"},
                {"type": "computed_userset", "relation": "member"},
            ],
        }
        rule = RewriteRule.from_dict(d)
        assert isinstance(rule, IntersectionRule)
        assert rule.to_dict() == d

    def test_from_dict_exclusion(self) -> None:
        d = {
            "type": "exclusion",
            "base": {"type": "computed_userset", "relation": "member"},
            "subtract": {"type": "computed_userset", "relation": "banned"},
        }
        rule = RewriteRule.from_dict(d)
        assert isinstance(rule, ExclusionRule)
        assert rule.to_dict() == d

    def test_from_dict_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown RewriteRule type"):
            RewriteRule.from_dict({"type": "unknown"})

    def test_from_dict_rejects_non_mapping_data(self) -> None:
        with pytest.raises(TypeError, match="dictionary"):
            RewriteRule.from_dict([])  # type: ignore[arg-type]


class TestRewriteRuleValidation:
    def test_composite_rules_reject_non_rule_children(self) -> None:
        with pytest.raises(TypeError, match="children must be rewrite rules"):
            UnionRule(children=(object(),))  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="children must be rewrite rules"):
            IntersectionRule(children=(object(),))  # type: ignore[arg-type]

    def test_exclusion_rejects_non_rule_operands(self) -> None:
        with pytest.raises(TypeError, match="base must be a rewrite rule"):
            ExclusionRule(
                base=object(),  # type: ignore[arg-type]
                subtract=ComputedUsersetRule("viewer"),
            )

        with pytest.raises(TypeError, match="subtract must be a rewrite rule"):
            ExclusionRule(
                base=ComputedUsersetRule("viewer"),
                subtract=object(),  # type: ignore[arg-type]
            )
