import pytest

from zanzipy.dsl.builder import DslCodec, NamespaceBuilder, SchemaBuilder
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.rules import (
    ComputedUsersetRule,
    ExclusionRule,
    IntersectionRule,
    TupleToUsersetRule,
    UnionRule,
)


class TestDslCodec:
    def test_parse_subject_ns(self):
        codec = DslCodec()
        s = codec.parse_subject("user")
        assert s.namespace.value == "user"
        assert s.relation is None
        assert s.wildcard is False

    def test_parse_subject_ns_rel(self):
        codec = DslCodec()
        s = codec.parse_subject("group#member")
        assert s.namespace.value == "group"
        assert s.relation is not None
        assert s.relation.value == "member"
        assert s.wildcard is False

    def test_parse_subject_wildcard(self):
        codec = DslCodec()
        s = codec.parse_subject("user:*")
        assert s.namespace.value == "user"
        assert s.relation is None
        assert s.wildcard is True

    def test_parse_subject_invalid(self):
        codec = DslCodec()
        with pytest.raises(ValueError, match="non-empty"):
            codec.parse_subject("")

    def test_to_subjects_mixed(self):
        codec = DslCodec()
        tup = codec.to_subjects(["user", "group#member", "folder:*"])
        assert len(tup) == 3

    def test_to_subjects_accepts_single_string(self):
        codec = DslCodec()
        tup = codec.to_subjects("user")
        assert len(tup) == 1
        assert tup[0].namespace.value == "user"

    def test_name_to_rule_computed(self):
        codec = DslCodec()
        r = codec.name_to_rule("viewer")
        assert isinstance(r, ComputedUsersetRule)
        assert r.relation == "viewer"

    def test_name_to_rule_ttu(self):
        codec = DslCodec()
        r = codec.name_to_rule("parent->viewer")
        assert isinstance(r, TupleToUsersetRule)
        assert r.tuple_relation == "parent"
        assert r.computed_relation == "viewer"

    def test_name_to_rule_ttu_invalid(self):
        codec = DslCodec()
        with pytest.raises(ValueError, match="tuple-to-userset shorthand"):
            codec.name_to_rule("parent->")

    def test_names_to_children(self):
        codec = DslCodec()
        cs = codec.names_to_children(["viewer", "parent->viewer"])
        assert isinstance(cs, tuple)
        assert len(cs) == 2
        assert isinstance(cs[0], ComputedUsersetRule)
        assert isinstance(cs[1], TupleToUsersetRule)

    def test_names_to_children_accepts_single_string(self):
        codec = DslCodec()
        cs = codec.names_to_children("viewer")
        assert len(cs) == 1
        assert isinstance(cs[0], ComputedUsersetRule)
        assert cs[0].relation == "viewer"


class TestNamespaceBuilder:
    def test_relation_direct_minimal(self):
        ns = (
            NamespaceBuilder("doc")
            .relation("owner", subjects=["user"])  # direct, no rewrite
            .build()
        )
        assert ns.name == "doc"
        assert "owner" in ns.relations
        assert ns.relations["owner"].rewrite is None

    def test_relation_with_rewrite_preserves_explicit_rewrite(self):
        rewrite = ComputedUsersetRule("owner")
        ns = (
            NamespaceBuilder("doc")
            .relation("owner", subjects="user")
            .relation("viewer", subjects="user", rewrite=rewrite)
            .build()
        )
        assert ns.relations["viewer"].rewrite is rewrite

    def test_permission_union(self):
        ns = (
            NamespaceBuilder("doc")
            .relation("owner", subjects=["user"])  # base
            .relation("viewer", subjects=["user"])  # referenced in permission
            .permission("can_view", union=["owner", "viewer"])  # strings
            .build()
        )
        assert "can_view" in ns.permissions
        rw = ns.permissions["can_view"].rewrite
        assert isinstance(rw, UnionRule)
        assert len(rw.children) == 2

    def test_permission_union_accepts_single_string(self):
        ns = (
            NamespaceBuilder("doc")
            .relation("viewer", subjects=["user"])
            .permission("can_view", union="viewer")
            .build()
        )
        rw = ns.permissions["can_view"].rewrite
        assert isinstance(rw, UnionRule)
        assert len(rw.children) == 1
        assert isinstance(rw.children[0], ComputedUsersetRule)
        assert rw.children[0].relation == "viewer"

    def test_permission_intersection(self):
        ns = (
            NamespaceBuilder("doc")
            .relation("member", subjects=["user"])  # base
            .relation("viewer", subjects=["user"])  # referenced in permission
            .permission("can_download", intersection=["viewer", "member"])
            .build()
        )
        rw = ns.permissions["can_download"].rewrite
        assert isinstance(rw, IntersectionRule)
        assert len(rw.children) == 2

    def test_permission_exclusion(self):
        ns = (
            NamespaceBuilder("doc")
            .relation("viewer", subjects=["user"])  # base
            .relation("banned", subjects=["user"])  # referenced in permission
            .permission("can_comment", exclusion=("viewer", "banned"))
            .build()
        )
        rw = ns.permissions["can_comment"].rewrite
        assert isinstance(rw, ExclusionRule)

    def test_permission_requires_one_operator(self):
        with pytest.raises(ValueError, match="Must specify exactly one"):
            NamespaceBuilder("doc").permission("x")

    def test_permission_rejects_empty_operator_operands(self):
        with pytest.raises(ValueError, match="union requires at least one operand"):
            NamespaceBuilder("doc").permission("x", union=[])

    def test_permission_exclusion_requires_two_operands(self):
        with pytest.raises(ValueError, match="exclusion requires exactly two operands"):
            NamespaceBuilder("doc").permission("x", exclusion=("viewer",))

    def test_permission_with_rewrite_explicit(self):
        ns = (
            NamespaceBuilder("doc")
            .relation("a", subjects=["user"])  # referenced by explicit rewrite
            .permission_with_rewrite(
                "p",
                UnionRule(children=(ComputedUsersetRule("a"),)),
            )
            .build()
        )
        assert "p" in ns.permissions


class TestSchemaBuilder:
    def test_register_many_and_validate(self):
        doc = NamespaceBuilder("doc").relation("viewer", subjects=["user"]).build()
        fold = NamespaceBuilder("folder").relation("viewer", subjects=["user"]).build()

        reg = (
            SchemaBuilder(SchemaRegistry())
            .add_namespace(doc)
            .add_namespace(fold)
            .build()
        )
        assert isinstance(reg, SchemaRegistry)
        assert set(reg.list_namespaces()) == {"doc", "folder"}

    def test_inline_namespace_flow(self):
        reg = (
            SchemaBuilder(SchemaRegistry())
            .namespace("doc")
            .relation("viewer", subjects=["user"])
            .permission("can_view", union=["viewer"])
            .done()
            .build()
        )
        assert isinstance(reg, SchemaRegistry)
        ns = reg.get_namespace("doc")
        assert "viewer" in ns.relations
        assert "can_view" in ns.permissions
