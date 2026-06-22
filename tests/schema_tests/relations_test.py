import pytest

from zanzipy.models.errors import IdentifierValidationError
from zanzipy.models.namespace import NamespaceId
from zanzipy.models.relation import Relation
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule, UnionRule
from zanzipy.schema.subjects import SubjectReference


class TestRelationDef:
    def test_accepts_single_subjectreference(self) -> None:
        subject = SubjectReference(namespace=NamespaceId("user"))
        rel = RelationDef(name="viewer", allowed_subjects=subject)
        assert rel.allowed_subjects == (
            SubjectReference(namespace=NamespaceId("user")),
        )

    def test_to_dict_minimal(self) -> None:
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        rel = RelationDef(name="viewer", allowed_subjects=subjects)
        assert rel.to_dict() == {
            "type": "relation",
            "name": "viewer",
            "allowed_subjects": [
                {"namespace": "user", "relation": None, "wildcard": False}
            ],
            "rewrite": None,
            "description": None,
        }

    def test_from_dict_minimal_roundtrip(self) -> None:
        data = {
            "type": "relation",
            "name": "viewer",
            "allowed_subjects": [
                {"namespace": "user", "relation": None, "wildcard": False}
            ],
            "rewrite": None,
            "description": None,
        }
        rel = RelationDef.from_dict(data)
        assert rel.to_dict() == data

    def test_to_dict_with_rewrite_and_description(self) -> None:
        subjects = (
            SubjectReference(namespace=NamespaceId("user")),
            SubjectReference(
                namespace=NamespaceId("group"), relation=Relation("member")
            ),
        )
        rewrite = UnionRule(
            children=(ComputedUsersetRule("owner"), ComputedUsersetRule("editor"))
        )
        rel = RelationDef(
            name="can_manage",
            allowed_subjects=subjects,
            rewrite=rewrite,
            description="Owners or editors",
        )
        assert rel.to_dict() == {
            "type": "relation",
            "name": "can_manage",
            "allowed_subjects": [
                {"namespace": "user", "relation": None, "wildcard": False},
                {"namespace": "group", "relation": "member", "wildcard": False},
            ],
            "rewrite": {
                "type": "union",
                "children": [
                    {"type": "computed_userset", "relation": "owner"},
                    {"type": "computed_userset", "relation": "editor"},
                ],
            },
            "description": "Owners or editors",
        }

    def test_from_dict_with_rewrite_roundtrip(self) -> None:
        data = {
            "type": "relation",
            "name": "can_manage",
            "allowed_subjects": [
                {"namespace": "user", "relation": None, "wildcard": False},
                {"namespace": "group", "relation": "member", "wildcard": False},
            ],
            "rewrite": {
                "type": "union",
                "children": [
                    {"type": "computed_userset", "relation": "owner"},
                    {"type": "computed_userset", "relation": "editor"},
                ],
            },
            "description": "Owners or editors",
        }
        rel = RelationDef.from_dict(data)
        assert rel.to_dict() == data

    def test_with_subjects_classmethod(self) -> None:
        subjects_tuple = (SubjectReference(namespace=NamespaceId("user")),)
        rel = RelationDef.with_subjects(name="viewer", subjects=subjects_tuple)
        assert rel.allowed_subjects == (
            SubjectReference(namespace=NamespaceId("user")),
        )

    def test_with_subjects_accepts_single_subjectreference(self) -> None:
        subject = SubjectReference(namespace=NamespaceId("user"))
        rel = RelationDef.with_subjects(name="viewer", subjects=subject)
        assert rel.allowed_subjects == (
            SubjectReference(namespace=NamespaceId("user")),
        )

    def test_requires_at_least_one_subject(self) -> None:
        with pytest.raises(
            ValueError, match="Relation must declare at least one allowed subject type"
        ):
            RelationDef(name="viewer", allowed_subjects=())

    @pytest.mark.parametrize(
        "name",
        [
            "viewer",
            "A",
            "_abc",
            "can-view-1",
        ],
    )
    def test_valid_names(self, name: str) -> None:
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        rel = RelationDef(name=name, allowed_subjects=subjects)
        assert rel.name == name

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "1starts_with_digit",
            "contains space",
            "bang!",
        ],
    )
    def test_invalid_names(self, name: str) -> None:
        subjects = (SubjectReference(namespace=NamespaceId("user")),)
        with pytest.raises(IdentifierValidationError):
            RelationDef(name=name, allowed_subjects=subjects)
