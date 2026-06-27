import pytest

from zanzipy.client import ZanzibarClient
from zanzipy.engine_integration import ZanzibarEngine, configure_authorization
from zanzipy.integration.decorators import authorizable_resource
from zanzipy.integration.mixins import AuthorizableGroup, AuthorizableSubject
from zanzipy.models.tuple import RelationTuple
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule, ExclusionRule, IntersectionRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.repos.concrete.memory.relations import (
    InMemoryRelationRepository,
)
from zanzipy.storage.revision import TupleMutation


def _registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    ns = NamespaceDef(
        name="doc",
        relations=(
            RelationDef.with_subjects(
                "owner", (SubjectReference.from_dict({"namespace": "user"}),)
            ),
            RelationDef.with_subjects(
                "viewer",
                (SubjectReference.from_dict({"namespace": "user"}),),
                rewrite=ComputedUsersetRule("owner"),
            ),
        ),
        permissions=(
            PermissionDef(name="view", rewrite=ComputedUsersetRule("viewer")),
        ),
    )
    reg.register(ns)
    return reg


def _group_registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register_many(
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
                    RelationDef.with_subjects(
                        "banned",
                        (SubjectReference.from_dict({"namespace": "user"}),),
                    ),
                ),
                permissions=(
                    PermissionDef(
                        name="owner_and_viewer",
                        rewrite=IntersectionRule(
                            children=(
                                ComputedUsersetRule("owner"),
                                ComputedUsersetRule("viewer"),
                            )
                        ),
                    ),
                    PermissionDef(
                        name="viewer_without_banned",
                        rewrite=ExclusionRule(
                            base=ComputedUsersetRule("viewer"),
                            subtract=ComputedUsersetRule("banned"),
                        ),
                    ),
                ),
            ),
        )
    )
    return reg


class TestAuthorizableResourceDecorator:
    def test_methods_injected_and_functional(self) -> None:
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=_registry())
        engine = ZanzibarEngine(client)
        configure_authorization(engine)

        @authorizable_resource("doc")
        class Doc:
            def __init__(self, id: str) -> None:
                self.id = id

        class User(AuthorizableSubject):
            def __init__(self, id: str) -> None:
                self.id = id

            def get_subject_dict(self) -> dict:
                return {"namespace": "user", "id": self.id}

        d = Doc("1")
        alice = User("alice")
        bob = User("bob")

        # grant via injected method
        d.grant(alice, "owner")  # type: ignore[attr-defined]
        assert d.check(alice, "view") is True  # type: ignore[attr-defined]
        assert d.check(bob, "view") is False  # type: ignore[attr-defined]

    def test_get_permissions_and_who_can(self) -> None:
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=_registry())
        engine = ZanzibarEngine(client)
        configure_authorization(engine)

        @authorizable_resource("doc")
        class Doc:
            def __init__(self, id: str) -> None:
                self.id = id

        class User(AuthorizableSubject):
            def __init__(self, id: str) -> None:
                self.id = id

            def get_subject_dict(self) -> dict:
                return {"namespace": "user", "id": self.id}

        d = Doc("1")
        alice = User("alice")

        # Seed tuple directly
        repo.write(
            (TupleMutation.touch(RelationTuple.from_string("doc:1#owner@user:alice")),)
        )

        perms = d.get_permissions(alice)  # type: ignore[attr-defined]
        assert "view" in perms

    def test_group_userset_permissions_through_mixins(self) -> None:
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=_group_registry())
        engine = ZanzibarEngine(client)
        configure_authorization(engine)

        @authorizable_resource("doc")
        class Doc:
            def __init__(self, id: str) -> None:
                self.id = id

        class User(AuthorizableSubject):
            def __init__(self, id: str) -> None:
                self.id = id

            def get_subject_dict(self) -> dict:
                return {"namespace": "user", "id": self.id}

        class Group(AuthorizableGroup):
            def __init__(self, id: str) -> None:
                self.id = id

            def get_resource_dict(self) -> dict:
                return {"namespace": "group", "id": self.id}

            def get_subject_dict(self) -> dict:
                return {"namespace": "group", "id": self.id, "relation": "member"}

        doc = Doc("1")
        eng = Group("eng")
        alice = User("alice")
        bob = User("bob")

        eng.add_member(alice)
        eng.add_member(bob)
        doc.grant(eng, "viewer")  # type: ignore[attr-defined]
        doc.grant(alice, "owner")  # type: ignore[attr-defined]
        doc.grant(alice, "banned")  # type: ignore[attr-defined]

        assert doc.check(alice, "owner_and_viewer") is True  # type: ignore[attr-defined]
        assert doc.check(bob, "owner_and_viewer") is False  # type: ignore[attr-defined]

        assert [str(s) for s in doc.who_can("owner_and_viewer")] == [  # type: ignore[attr-defined]
            "user:alice"
        ]
        assert [str(s) for s in doc.who_can("viewer_without_banned")] == [  # type: ignore[attr-defined]
            "user:bob"
        ]
        accessible = bob.get_accessible("doc", "viewer_without_banned")
        assert [str(obj) for obj in accessible] == ["doc:1"]


class TestMixinContracts:
    def test_get_accessible_preserves_zero_limit(self) -> None:
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=_group_registry())
        engine = ZanzibarEngine(client)
        configure_authorization(engine)

        @authorizable_resource("doc")
        class Doc:
            def __init__(self, id: str) -> None:
                self.id = id

        class User(AuthorizableSubject):
            def __init__(self, id: str) -> None:
                self.id = id

            def get_subject_dict(self) -> dict:
                return {"namespace": "user", "id": self.id}

        doc = Doc("1")
        bob = User("bob")
        doc.grant(bob, "owner")  # type: ignore[attr-defined]

        assert bob.get_accessible("doc", "owner", limit=0) == []

    def test_reference_dicts_require_namespace_and_id(self) -> None:
        class BadResource(AuthorizableGroup):
            def get_resource_dict(self) -> dict:
                return {"namespace": "group"}

            def get_subject_dict(self) -> dict:
                return {"namespace": "group", "id": "eng"}

        class BadSubject(AuthorizableSubject):
            def get_subject_dict(self) -> dict:
                return {"id": "alice"}

        with pytest.raises(ValueError, match="missing required key 'id'"):
            BadResource().get_resource_ref()
        with pytest.raises(ValueError, match="missing required key 'namespace'"):
            BadSubject().get_subject_ref()

    def test_decorated_resource_can_be_granted_as_subject(self) -> None:
        reg = SchemaRegistry()
        reg.register(
            NamespaceDef(
                name="doc",
                relations=(
                    RelationDef.with_subjects(
                        "parent",
                        (SubjectReference.from_dict({"namespace": "doc"}),),
                    ),
                ),
                permissions=(
                    PermissionDef(name="view", rewrite=ComputedUsersetRule("parent")),
                ),
            )
        )
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=reg)
        configure_authorization(ZanzibarEngine(client))

        @authorizable_resource("doc")
        class Doc:
            def __init__(self, id: str) -> None:
                self.id = id

        child = Doc("child")
        parent = Doc("parent")

        child.grant(parent, "parent")  # type: ignore[attr-defined]

        assert client.check("doc:child", "parent", "doc:parent")
