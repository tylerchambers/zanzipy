from zanzipy.client import ZanzibarClient
from zanzipy.engine_integration import ZanzibarEngine, configure_authorization
from zanzipy.integration.decorators import authorizable_resource
from zanzipy.integration.mixins import AuthorizableSubject
from zanzipy.models.tuple import RelationTuple
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.repos.concrete.memory.relations import (
    InMemoryRelationRepository,
)


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
        repo.write(RelationTuple.from_string("doc:1#owner@user:alice"))

        perms = d.get_permissions(alice)  # type: ignore[attr-defined]
        assert "view" in perms
