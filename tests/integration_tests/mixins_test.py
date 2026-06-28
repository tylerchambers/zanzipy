import pytest

from zanzipy.client import ZanzibarClient
from zanzipy.engine_integration import ZanzibarEngine, configure_authorization
from zanzipy.integration.mixins import (
    AuthorizableGroup,
    AuthorizableResource,
    AuthorizableSubject,
    _normalize_to_subject,
    _required_ref_value,
)
from zanzipy.models import Obj, Subject
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository


class _Doc(AuthorizableResource):
    def __init__(self, id: object) -> None:
        self.id = id

    def get_resource_dict(self) -> dict:
        return {"namespace": "doc", "id": self.id}


class _User(AuthorizableSubject):
    def __init__(self, id: object) -> None:
        self.id = id

    def get_subject_dict(self) -> dict:
        return {"namespace": "user", "id": self.id}


class _Group(AuthorizableGroup):
    def __init__(self, id: object) -> None:
        self.id = id

    def get_resource_dict(self) -> dict:
        return {"namespace": "group", "id": self.id}

    def get_subject_dict(self) -> dict:
        return {"namespace": "group", "id": self.id, "relation": "member"}


def _registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register_many(
        (
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "member",
                        (SubjectReference.from_dict({"namespace": "user"}),),
                    ),
                ),
                permissions=(),
            ),
            NamespaceDef(
                name="doc",
                relations=(
                    RelationDef.with_subjects(
                        "owner",
                        (SubjectReference.from_dict({"namespace": "user"}),),
                    ),
                    RelationDef.with_subjects(
                        "viewer",
                        (
                            SubjectReference.from_dict(
                                {"namespace": "group", "relation": "member"}
                            ),
                        ),
                    ),
                ),
                permissions=(
                    PermissionDef(name="view", rewrite=ComputedUsersetRule("viewer")),
                ),
            ),
        )
    )
    return registry


def _configure_engine() -> ZanzibarClient:
    client = ZanzibarClient(
        relations_repository=InMemoryRelationRepository(), schema=_registry()
    )
    configure_authorization(ZanzibarEngine(client))
    return client


class TestMixinReferenceDefaults:
    def test_default_refs_coerce_dictionary_values(self) -> None:
        class RelationValue:
            def __str__(self) -> str:
                return "member"

        class NumericSubject(AuthorizableSubject):
            def get_subject_dict(self) -> dict:
                return {"namespace": "group", "id": 7, "relation": RelationValue()}

        assert str(_Doc(123).get_resource_ref()) == "doc:123"
        assert str(NumericSubject().get_subject_ref()) == "group:7#member"

    def test_unimplemented_reference_dicts_raise_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            AuthorizableResource().get_resource_ref()
        with pytest.raises(NotImplementedError):
            AuthorizableSubject().get_subject_ref()

    def test_required_ref_values_reject_missing_and_none(self) -> None:
        with pytest.raises(ValueError, match="missing required key 'namespace'"):
            _required_ref_value({"id": "1"}, "namespace")
        with pytest.raises(ValueError, match="key 'id' cannot be None"):
            _required_ref_value({"id": None}, "id")


class TestSubjectNormalization:
    def test_normalize_accepts_subject_resource_value_and_duck_types(self) -> None:
        subject = Subject.from_string("user:alice")
        assert _normalize_to_subject(subject) is subject
        assert str(_normalize_to_subject(Obj.from_string("doc:1"))) == "doc:1"
        assert str(_normalize_to_subject(_User("bob"))) == "user:bob"
        assert str(_normalize_to_subject(_Doc("readme"))) == "doc:readme"
        assert str(_normalize_to_subject("user:carol")) == "user:carol"

        class SubjectDuck:
            def get_subject_ref(self) -> Subject:
                return Subject.from_string("user:dora")

        class ResourceDuck:
            def get_resource_ref(self) -> Obj:
                return Obj.from_string("doc:duck")

        assert str(_normalize_to_subject(SubjectDuck())) == "user:dora"
        assert str(_normalize_to_subject(ResourceDuck())) == "doc:duck"

    def test_normalize_rejects_invalid_duck_return_values(self) -> None:
        class BadSubjectDuck:
            def get_subject_ref(self) -> str:
                return "user:alice"

        with pytest.raises(TypeError, match="subject must be"):
            _normalize_to_subject(BadSubjectDuck())


class TestSubjectAndGroupHelpers:
    def test_subject_helpers_and_group_membership_round_trip(self) -> None:
        _configure_engine()
        doc = _Doc("1")
        group = _Group("eng")
        alice = _User("alice")
        bob = _User("bob")

        group.add_member(bob)
        doc.grant(group, "viewer")
        doc.grant(alice, "owner")

        assert bob.can(doc, "view") is True
        assert alice.can(doc, "owner") is True
        assert [str(obj) for obj in bob.get_accessible("doc", "view")] == ["doc:1"]
        assert bob.get_relations(group) == {"member"}
        assert group.is_member(bob) is True
        assert [str(member) for member in group.get_members()] == ["user:bob"]

        group.remove_member(bob)

        assert group.is_member(bob) is False
        assert bob.get_relations(group) == set()
