import pytest

from zanzipy.client import ZanzibarClient
from zanzipy.models.errors import IdentifierValidationError, InvalidTupleFormatError
from zanzipy.models.tuple import RelationTuple
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule, TupleToUsersetRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.repos.concrete.memory.relations import (
    InMemoryRelationRepository,
)


class TestZanzibarClient:
    def _base_registry(self) -> SchemaRegistry:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "owner", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
                RelationDef.with_subjects(
                    "editor", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
                # allow group#member subject sets for testing
                RelationDef.with_subjects(
                    "member",
                    (
                        SubjectReference.from_dict(
                            {"namespace": "group", "relation": "member"}
                        ),
                    ),
                ),
                # relation used for tuple-to-userset
                RelationDef.with_subjects(
                    "parent", (SubjectReference.from_dict({"namespace": "folder"}),)
                ),
                # a plain viewer relation to satisfy tuple-to-userset target
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(
                PermissionDef(name="can_view", rewrite=ComputedUsersetRule("owner")),
            ),
        )
        folder = NamespaceDef(
            name="folder",
            relations=(
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(),
        )
        registry.register_many([ns, folder])
        return registry

    def test_write_and_check_happy_path(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        client.write("document:doc1", "owner", "user:alice")
        assert repo.get(RelationTuple.from_string("document:doc1#owner@user:alice"))

        assert client.check("document:doc1", "owner", "user:alice") is True
        assert client.check("document:doc1", "owner", "user:bob") is False

    def test_write_rejects_permission_and_unknowns(self) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "owner", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(
                PermissionDef(name="can_view", rewrite=ComputedUsersetRule("owner")),
            ),
        )
        registry.register(ns)
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        # Cannot write to a permission
        with pytest.raises(ValueError, match="Cannot write to permission"):
            client.write("document:doc1", "can_view", "user:alice")

        # Unknown relation should surface as ValueError from registry
        with pytest.raises(ValueError, match="Unknown relation or permission"):
            client.write("document:doc1", "missing", "user:alice")

    def test_write_subject_validation(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        # Allowed: group subject set only on 'member' relation
        client.write("document:d1", "member", "group:eng#member")
        # Not allowed: group subject set on 'owner'
        with pytest.raises(ValueError, match="Subject not allowed by schema"):
            client.write("document:d2", "owner", "group:eng#member")

        # Bad tuple formatting via components (e.g., missing ':')
        with pytest.raises(InvalidTupleFormatError):
            client.write("document", "owner", "user:alice")

    def test_write_many_all_or_nothing(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        # Second tuple invalid by schema -> whole call raises, nothing written
        tuples = [
            ("document:ok", "owner", "user:alice"),
            ("document:bad", "owner", "group:eng#member"),
        ]
        with pytest.raises(ValueError, match="Subject not allowed by schema"):
            client.write_many(tuples)
        # No tuples persisted
        assert (
            list(repo.find(object())) == []  # type: ignore
        )  # TupleFilter fields all None analogue

        # All good -> both written
        client.write_many(
            [
                ("document:a", "owner", "user:alice"),
                ("document:b", "owner", "user:alice"),
            ]
        )
        assert client.check("document:a", "owner", "user:alice") is True
        assert client.check("document:b", "owner", "user:alice") is True

    def test_delete_returns_bool(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        client.write("document:doc1", "owner", "user:alice")
        assert client.delete("document:doc1", "owner", "user:alice") is True
        # Deleting again should return False
        assert client.delete("document:doc1", "owner", "user:alice") is False

    def test_check_rejects_subject_sets(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        with pytest.raises(ValueError, match="direct subject"):
            client.check("document:doc1", "owner", "group:eng#member")

    def test_check_detailed_debug_toggle(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()

        # No debug
        client = ZanzibarClient(relations_repository=repo, schema=registry)
        client.write("document:doc1", "owner", "user:alice")
        res = client.check_detailed("document:doc1", "owner", "user:alice")
        assert res.allowed is True
        assert res.debug_trace is None

        # With debug
        client_dbg = ZanzibarClient(
            relations_repository=repo, schema=registry, enable_debug=True
        )
        res_dbg = client_dbg.check_detailed("document:doc1", "owner", "user:alice")
        assert res_dbg.allowed is True
        assert isinstance(res_dbg.debug_trace, list)

    def test_list_objects_happy_and_errors(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        # owner yields can_view via permission
        client.write("document:d1", "owner", "user:alice")
        client.write("document:d2", "owner", "user:bob")
        # unrelated object type
        client.write("folder:f1", "viewer", "user:alice")

        docs_for_alice = client.list_objects("document", "can_view", "user:alice")
        assert set(docs_for_alice) == {"document:d1"}

        docs_for_bob = client.list_objects("document", "can_view", "user:bob")
        assert set(docs_for_bob) == {"document:d2"}

        # Subject must be direct
        with pytest.raises(ValueError, match="direct subject"):
            client.list_objects("document", "can_view", "group:eng#member")
        # Invalid namespace identifier
        with pytest.raises(IdentifierValidationError):
            client.list_objects("bad ns", "can_view", "user:alice")

    def test_list_subjects_direct(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        client.write("document:doc", "owner", "user:alice")
        client.write("document:doc", "owner", "user:bob")
        client.write("document:doc", "member", "group:eng#member")

        subjects = set(client.list_subjects_direct("document:doc", "owner"))
        assert subjects == {"user:alice", "user:bob"}

        subjects_any = set(client.list_subjects_direct("document:doc", "member"))
        assert subjects_any == {"group:eng#member"}

    def test_ping_and_close(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(
            relations_repository=repo,
            schema=registry,
        )

        # Ping succeeds based on relations repo only
        assert client.ping() is True

        # Close should call close() on relations repo without error
        client.close()

    def test_tuple_to_userset_flow(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        # document.parent -> folder; folder.viewer -> user
        client.write("document:d1", "parent", "folder:f1")
        client.write("folder:f1", "viewer", "user:alice")

        # Permission defined in base registry for tuple_to_userset
        reg2 = SchemaRegistry()
        # document namespace already in registry, but ensure can_view via
        # tuple_to_userset for this test
        folder_ns = NamespaceDef(
            name="folder",
            relations=(
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(),
        )

        document_ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "parent",
                    (SubjectReference.from_dict({"namespace": "folder"}),),
                ),
                RelationDef.with_subjects(
                    "viewer",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(
                PermissionDef(
                    name="can_view",
                    rewrite=TupleToUsersetRule(
                        tuple_relation="parent", computed_relation="viewer"
                    ),
                ),
            ),
        )

        reg2.register_many([folder_ns, document_ns])
        client2 = ZanzibarClient(relations_repository=repo, schema=reg2)
        assert client2.check("document:d1", "can_view", "user:alice") is True
        assert client2.check("document:d1", "can_view", "user:bob") is False

    def test_expand_subject_sets_and_users(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        # Direct users and subject-set
        client.write("document:doc", "owner", "user:alice")
        client.write("document:doc", "owner", "user:bob")
        # allow group member subject set on 'member' relation per _base_registry
        client.write("document:doc", "member", "group:eng#member")

        # Expand 'owner' -> only users bucket
        owners = client.expand("document:doc", "owner")
        assert owners.users == {"user:alice", "user:bob"}
        assert owners.usersets == set()

        # Expand 'member' -> usersets bucket sees the subject-set anchor
        members = client.expand("document:doc", "member")
        assert members.users == set()
        assert members.usersets == {"group:eng#member"}

    def test_expand_tuple_to_userset(self) -> None:
        registry = self._base_registry()
        repo = InMemoryRelationRepository()
        client = ZanzibarClient(relations_repository=repo, schema=registry)

        # document.parent -> folder; folder.viewer -> user
        client.write("document:d1", "parent", "folder:f1")
        client.write("folder:f1", "viewer", "user:alice")

        # Build a schema where document.can_view = parent->viewer
        reg2 = SchemaRegistry()
        folder_ns = NamespaceDef(
            name="folder",
            relations=(
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(),
        )
        document_ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "parent",
                    (SubjectReference.from_dict({"namespace": "folder"}),),
                ),
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(
                PermissionDef(
                    name="can_view",
                    rewrite=TupleToUsersetRule(
                        tuple_relation="parent", computed_relation="viewer"
                    ),
                ),
            ),
        )
        reg2.register_many([folder_ns, document_ns])
        client2 = ZanzibarClient(relations_repository=repo, schema=reg2)

        sset = client2.expand("document:d1", "can_view")
        assert sset.users == {"user:alice"}
