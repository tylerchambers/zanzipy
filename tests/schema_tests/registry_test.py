import pytest

from zanzipy.models.namespace import NamespaceId
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule, TupleToUsersetRule, UnionRule
from zanzipy.schema.subjects import SubjectReference


def _basic_namespace() -> NamespaceDef:
    subjects = (SubjectReference(namespace=NamespaceId("user")),)
    rels = (
        RelationDef(name="owner", allowed_subjects=subjects),
        RelationDef(name="editor", allowed_subjects=subjects),
        RelationDef(
            name="viewer",
            allowed_subjects=subjects,
            rewrite=UnionRule(
                children=(
                    ComputedUsersetRule("owner"),
                    ComputedUsersetRule("editor"),
                )
            ),
        ),
    )
    perms = (PermissionDef(name="can_view", rewrite=ComputedUsersetRule("viewer")),)
    return NamespaceDef(name="document", relations=rels, permissions=perms)


class TestSchemaRegistry:
    def test_register_and_get_namespace(self) -> None:
        registry = SchemaRegistry()
        ns = _basic_namespace()
        registry.register(ns)

        got = registry.get_namespace("document")
        assert got.name == "document"
        assert set(got.relations.keys()) == {"owner", "editor", "viewer"}
        assert set(got.permissions.keys()) == {"can_view"}

    def test_register_many_and_list(self) -> None:
        registry = SchemaRegistry()
        doc_ns = _basic_namespace()

        # second namespace
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        rels = (RelationDef(name="member", allowed_subjects=subjects),)
        group_ns = NamespaceDef(name="group", relations=rels, permissions=())

        registry.register_many([doc_ns, group_ns])

        names = registry.list_namespaces()
        assert names == ["document", "group"]

    def test_get_namespace_unknown_raises(self) -> None:
        registry = SchemaRegistry()
        with pytest.raises(ValueError, match=r"Unknown namespace: missing"):
            registry.get_namespace("missing")

    def test_get_relation_definition_for_relation_and_permission(self) -> None:
        registry = SchemaRegistry()
        ns = _basic_namespace()
        registry.register(ns)

        rel_def = registry.get_relation_definition("document", "viewer")
        perm_def = registry.get_relation_definition("document", "can_view")
        assert rel_def["type"] == "relation"
        assert rel_def["name"] == "viewer"
        assert perm_def["type"] == "permission"
        assert perm_def["name"] == "can_view"

    def test_get_relation_definition_unknown_relation_raises(self) -> None:
        registry = SchemaRegistry()
        ns = _basic_namespace()
        registry.register(ns)
        with pytest.raises(
            ValueError,
            match=r"Unknown relation or permission 'missing' in namespace 'document'",
        ):
            registry.get_relation_definition("document", "missing")

    def test_get_relation_definition_unknown_namespace_raises(self) -> None:
        registry = SchemaRegistry()
        with pytest.raises(ValueError, match=r"Unknown namespace: nope"):
            registry.get_relation_definition("nope", "viewer")

    def test_validate_all_ok(self) -> None:
        registry = SchemaRegistry()
        registry.register(_basic_namespace())
        # Should not raise
        registry.validate_all()

    def test_update_namespace_success(self) -> None:
        registry = SchemaRegistry()
        base = _basic_namespace()
        registry.register(base)

        # Modify: add a permission
        updated = NamespaceDef(
            name=base.name,
            relations=tuple(base.relations.values()),
            permissions=(
                *tuple(base.permissions.values()),
                PermissionDef(name="can_edit", rewrite=ComputedUsersetRule("editor")),
            ),
            description=base.description,
        )
        registry.update_namespace(updated)

        got = registry.get_namespace("document")
        assert set(got.permissions.keys()) == {"can_view", "can_edit"}

    def test_update_namespace_unknown_raises(self) -> None:
        registry = SchemaRegistry()
        with pytest.raises(ValueError, match=r"Unknown namespace: document"):
            registry.update_namespace(_basic_namespace())

    def test_update_many_success(self) -> None:
        registry = SchemaRegistry()
        base = _basic_namespace()
        registry.register(base)

        # second namespace registered as well
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        group = NamespaceDef(
            name="group",
            relations=(RelationDef(name="member", allowed_subjects=subjects),),
            permissions=(),
        )
        registry.register(group)

        # Prepare updates: add relation to document, add permission to group
        updated_doc = NamespaceDef(
            name=base.name,
            relations=(
                *tuple(base.relations.values()),
                RelationDef(name="contributor", allowed_subjects=subjects),
            ),
            permissions=tuple(base.permissions.values()),
            description=base.description,
        )
        updated_group = NamespaceDef(
            name=group.name,
            relations=tuple(group.relations.values()),
            permissions=(
                PermissionDef(name="can_invite", rewrite=ComputedUsersetRule("member")),
            ),
            description=group.description,
        )

        registry.update_many([updated_doc, updated_group])

        got_doc = registry.get_namespace("document")
        got_group = registry.get_namespace("group")
        assert "contributor" in got_doc.relations
        assert "can_invite" in got_group.permissions

    def test_update_many_unknown_raises(self) -> None:
        registry = SchemaRegistry()
        base = _basic_namespace()
        # Not registered
        with pytest.raises(ValueError, match=r"Unknown namespace: document"):
            registry.update_many([base])

    def test_register_many_validates_tuple_to_userset_target_namespace(self) -> None:
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        folder = NamespaceDef(
            name="folder",
            relations=(RelationDef(name="viewer", allowed_subjects=subjects),),
        )
        document = NamespaceDef(
            name="document",
            relations=(
                RelationDef(
                    name="parent",
                    allowed_subjects=(
                        SubjectReference(namespace=NamespaceId("folder")),
                    ),
                ),
            ),
            permissions=(
                PermissionDef(
                    name="can_view",
                    rewrite=TupleToUsersetRule(
                        tuple_relation="parent",
                        computed_relation="viewer",
                    ),
                ),
            ),
        )
        registry = SchemaRegistry()

        registry.register_many([document, folder])

        assert registry.list_namespaces() == ["document", "folder"]

    def test_register_many_rejects_missing_tuple_to_userset_target(self) -> None:
        document = NamespaceDef(
            name="document",
            relations=(
                RelationDef(
                    name="parent",
                    allowed_subjects=(
                        SubjectReference(namespace=NamespaceId("folder")),
                    ),
                ),
            ),
            permissions=(
                PermissionDef(
                    name="can_view",
                    rewrite=TupleToUsersetRule(
                        tuple_relation="parent",
                        computed_relation="viewer",
                    ),
                ),
            ),
        )
        registry = SchemaRegistry()

        with pytest.raises(
            ValueError,
            match=r"folder#viewer'.*not a known relation or permission",
        ):
            registry.register_many([document, NamespaceDef(name="folder")])

        assert registry.list_namespaces() == []

    def test_register_rejects_missing_subject_set_namespace(self) -> None:
        document = NamespaceDef(
            name="document",
            relations=(
                RelationDef(
                    name="viewer",
                    allowed_subjects=(
                        SubjectReference(
                            namespace=NamespaceId("group"),
                            relation="member",
                        ),
                    ),
                ),
            ),
        )
        registry = SchemaRegistry()

        with pytest.raises(
            ValueError,
            match=r"allowed subject namespace 'group' is not registered",
        ):
            registry.register(document)

    def test_diff_namespaces(self) -> None:
        old = _basic_namespace()
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        new_relations = (
            *(
                RelationDef(
                    name="viewer",
                    allowed_subjects=subjects,
                    rewrite=ComputedUsersetRule("owner"),
                )
                if relation.name == "viewer"
                else relation
                for relation in old.relations.values()
            ),
            RelationDef(name="contributor", allowed_subjects=subjects),
        )
        new = NamespaceDef(
            name=old.name,
            relations=new_relations,
            permissions=(),  # remove can_view
            description="docs",
        )

        diff = SchemaRegistry.diff_namespaces(old, new)
        assert diff["top_level"]["description_changed"] is True
        assert "contributor" in diff["relations"]["added"]
        assert "can_view" in diff["permissions"]["removed"]
        assert "viewer" in diff["relations"]["changed"]
