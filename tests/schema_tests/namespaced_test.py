import pytest

from zanzipy.models.errors import IdentifierValidationError
from zanzipy.models.namespace import NamespaceId
from zanzipy.models.relation import Relation
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import (
    ComputedUsersetRule,
    DirectRule,
    IntersectionRule,
    ThisRule,
    TupleToUsersetRule,
    UnionRule,
)
from zanzipy.schema.subjects import SubjectReference


class TestNamespaceDef:
    def test_to_dict_minimal(self) -> None:
        ns = NamespaceDef(name="document")
        assert ns.to_dict() == {
            "name": "document",
            "description": None,
            "relations": {},
            "permissions": {},
        }

    def test_from_dict_minimal_roundtrip(self) -> None:
        data = {
            "name": "document",
            "description": None,
            "relations": {},
            "permissions": {},
        }
        ns = NamespaceDef.from_dict(data)
        assert ns.to_dict() == data

    def test_from_dict_rejects_empty_allowed_subject_relation(self) -> None:
        data = {
            "name": "document",
            "description": None,
            "relations": {
                "viewer": {
                    "type": "relation",
                    "name": "viewer",
                    "allowed_subjects": [
                        {"namespace": "group", "relation": "", "wildcard": False}
                    ],
                    "rewrite": None,
                    "description": None,
                }
            },
            "permissions": {},
        }

        with pytest.raises(IdentifierValidationError):
            NamespaceDef.from_dict(data)

    def test_from_dict_rejects_falsy_invalid_relation_rewrite(self) -> None:
        data = {
            "name": "document",
            "description": None,
            "relations": {
                "viewer": {
                    "type": "relation",
                    "name": "viewer",
                    "allowed_subjects": [
                        {"namespace": "user", "relation": None, "wildcard": False}
                    ],
                    "rewrite": {},
                    "description": None,
                }
            },
            "permissions": {},
        }

        with pytest.raises(ValueError, match="Unknown RewriteRule type"):
            NamespaceDef.from_dict(data)

    def test_full_roundtrip(self) -> None:
        subjects_user = (SubjectReference(namespace=NamespaceId("user")),)
        subjects_group_member = (
            SubjectReference(
                namespace=NamespaceId("group"), relation=Relation("member")
            ),
        )

        relations = (
            RelationDef(name="owner", allowed_subjects=subjects_user),
            RelationDef(name="editor", allowed_subjects=subjects_user),
            RelationDef(
                name="parent",
                allowed_subjects=(SubjectReference(namespace=NamespaceId("document")),),
            ),
            RelationDef(
                name="viewer",
                allowed_subjects=subjects_user + subjects_group_member,
                rewrite=UnionRule(children=(ThisRule(), ComputedUsersetRule("editor"))),
                description="viewers include direct and editors",
            ),
        )

        permissions = (
            PermissionDef(
                name="can_view",
                rewrite=UnionRule(
                    children=(
                        ComputedUsersetRule("viewer"),
                        ComputedUsersetRule("owner"),
                    )
                ),
            ),
            PermissionDef(
                name="can_admin",
                rewrite=IntersectionRule(
                    children=(
                        ComputedUsersetRule("owner"),
                        ComputedUsersetRule("editor"),
                    )
                ),
            ),
            PermissionDef(
                name="can_inherit_viewer",
                rewrite=TupleToUsersetRule(
                    tuple_relation="parent", computed_relation="viewer"
                ),
            ),
        )

        ns = NamespaceDef(name="document", relations=relations, permissions=permissions)
        # Round-trip should be stable
        roundtrip = NamespaceDef.from_dict(ns.to_dict()).to_dict()
        assert roundtrip == ns.to_dict()

    def test_invalid_namespace_name(self) -> None:
        with pytest.raises(IdentifierValidationError):
            NamespaceDef(name="")
        with pytest.raises(IdentifierValidationError):
            NamespaceDef(name=" bad")

    def test_overlap_names_between_relation_and_permission_raises(self) -> None:
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        rel = RelationDef(name="viewer", allowed_subjects=subjects)
        perm = PermissionDef(name="viewer", rewrite=ComputedUsersetRule("viewer"))
        with pytest.raises(ValueError, match=r"Names cannot be both relation"):
            NamespaceDef(
                name="document",
                relations=(rel,),
                permissions=(perm,),
            )

    def test_duplicate_relation_names_raise(self) -> None:
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        relation = RelationDef(name="viewer", allowed_subjects=subjects)
        duplicate = RelationDef(name="viewer", allowed_subjects=subjects)

        with pytest.raises(ValueError, match=r"Duplicate relation names"):
            NamespaceDef(name="document", relations=(relation, duplicate))

    def test_duplicate_permission_names_raise(self) -> None:
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        relation = RelationDef(name="viewer", allowed_subjects=subjects)
        permission = PermissionDef(
            name="can_view", rewrite=ComputedUsersetRule("viewer")
        )
        duplicate = PermissionDef(
            name="can_view", rewrite=ComputedUsersetRule("viewer")
        )

        with pytest.raises(ValueError, match=r"Duplicate permission names"):
            NamespaceDef(
                name="document",
                relations=(relation,),
                permissions=(permission, duplicate),
            )

    def test_definition_maps_are_immutable(self) -> None:
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        ns = NamespaceDef(
            name="document",
            relations=(RelationDef(name="viewer", allowed_subjects=subjects),),
        )

        with pytest.raises(TypeError):
            ns.relations["owner"] = RelationDef(  # type: ignore[index]
                name="owner",
                allowed_subjects=subjects,
            )

    def test_permission_forbids_this_and_direct(self) -> None:
        with pytest.raises(ValueError, match=r"not valid in permissions"):
            NamespaceDef(
                name="doc",
                permissions=(PermissionDef(name="p", rewrite=ThisRule()),),
            )
        with pytest.raises(ValueError, match=r"not valid in permissions"):
            NamespaceDef(
                name="doc",
                permissions=(PermissionDef(name="p", rewrite=DirectRule()),),
            )

        # Sanity: a valid permission referencing an existing relation passes
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        rel = RelationDef(name="viewer", allowed_subjects=subjects)
        perm = PermissionDef(name="can_view", rewrite=ComputedUsersetRule("viewer"))
        ns = NamespaceDef(name="doc", relations=(rel,), permissions=(perm,))
        assert "viewer" in ns.relations
        assert "can_view" in ns.permissions

    def test_permission_computed_userset_unknown_raises(self) -> None:
        perm = PermissionDef(name="p", rewrite=ComputedUsersetRule("missing"))
        with pytest.raises(ValueError, match=r"computed userset references unknown"):
            NamespaceDef(name="doc", permissions=(perm,))

    def test_permission_tuple_to_userset_unknown_relations_raise(self) -> None:
        perm = PermissionDef(
            name="p",
            rewrite=TupleToUsersetRule(
                tuple_relation="missing", computed_relation="viewer"
            ),
        )
        with pytest.raises(
            ValueError, match=r"tuple_to_userset\.tuple_relation 'missing'"
        ):
            NamespaceDef(name="doc", permissions=(perm,))

        perm2 = PermissionDef(
            name="p2",
            rewrite=TupleToUsersetRule(
                tuple_relation="parent", computed_relation="missing"
            ),
        )
        parent_rel = RelationDef(
            name="parent",
            allowed_subjects=(SubjectReference(namespace=NamespaceId("doc")),),
        )

        ns = NamespaceDef(
            name="doc",
            relations=(parent_rel,),
            permissions=(perm2,),
        )
        assert "p2" in ns.permissions

    def test_relation_computed_userset_unknown_raises(self) -> None:
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        rel = RelationDef(
            name="viewer",
            allowed_subjects=subjects,
            rewrite=ComputedUsersetRule("does_not_exist"),
        )
        with pytest.raises(ValueError, match=r"computed userset references unknown"):
            NamespaceDef(name="doc", relations=(rel,))

    def test_relation_tuple_to_userset_unknown_raises(self) -> None:
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        parent = RelationDef(
            name="parent",
            allowed_subjects=(SubjectReference(namespace=NamespaceId("doc")),),
        )
        viewer = RelationDef(name="viewer", allowed_subjects=subjects)

        bad1 = RelationDef(
            name="bad1",
            allowed_subjects=subjects,
            rewrite=TupleToUsersetRule(
                tuple_relation="missing", computed_relation="viewer"
            ),
        )
        with pytest.raises(
            ValueError, match=r"tuple_to_userset\.tuple_relation 'missing'"
        ):
            NamespaceDef(
                name="doc",
                relations=(parent, viewer, bad1),
            )

        bad2 = RelationDef(
            name="bad2",
            allowed_subjects=subjects,
            rewrite=TupleToUsersetRule(
                tuple_relation="parent", computed_relation="missing"
            ),
        )
        ns = NamespaceDef(
            name="doc",
            relations=(parent, viewer, bad2),
        )
        assert "bad2" in ns.relations

    def test_from_dict_key_mismatch_raises(self) -> None:
        data = {
            "name": "doc",
            "description": None,
            "relations": {
                "viewer": {
                    "type": "relation",
                    "name": "wrong",
                    "allowed_subjects": [
                        {"namespace": "user", "relation": None, "wildcard": False}
                    ],
                    "rewrite": None,
                    "description": None,
                }
            },
            "permissions": {},
        }
        with pytest.raises(ValueError, match=r"Relation mapping key 'viewer'"):
            NamespaceDef.from_dict(data)
