import pytest

from zanzipy.client import ZanzibarClient
from zanzipy.models import RelationTuple
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import TupleToUsersetRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository
from zanzipy.storage.revision import TenantId, TupleMutation, WriteContext

DEFAULT_TENANT = TenantId("default")


def _user_ref() -> SubjectReference:
    return SubjectReference.from_dict({"namespace": "user"})


def _user_wildcard_ref() -> SubjectReference:
    return SubjectReference.from_dict({"namespace": "user", "wildcard": True})


def _group_member_ref() -> SubjectReference:
    return SubjectReference.from_dict({"namespace": "group", "relation": "member"})


def _schema(*viewer_subjects: SubjectReference) -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(RelationDef.with_subjects("member", (_user_ref(),)),),
            ),
            NamespaceDef(
                name="document",
                relations=(RelationDef.with_subjects("viewer", viewer_subjects),),
            ),
        ]
    )
    return registry


def _inherited_schema(parent_subject: SubjectReference) -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="folder",
                relations=(RelationDef.with_subjects("viewer", (_user_ref(),)),),
            ),
            NamespaceDef(
                name="project",
                relations=(RelationDef.with_subjects("viewer", (_user_ref(),)),),
            ),
            NamespaceDef(
                name="document",
                relations=(RelationDef.with_subjects("parent", (parent_subject,)),),
                permissions=(
                    PermissionDef("can_view", TupleToUsersetRule("parent", "viewer")),
                ),
            ),
        ]
    )
    return registry


def _client(
    repo: InMemoryRelationRepository,
    schema: SchemaRegistry,
) -> ZanzibarClient:
    return ZanzibarClient(
        relations_repository=repo,
        schema=schema,
        tenant=DEFAULT_TENANT,
    )


def _write_raw(repo: InMemoryRelationRepository, *tuples: str) -> None:
    repo.write(
        WriteContext(DEFAULT_TENANT),
        tuple(
            TupleMutation.touch(RelationTuple.from_string(relation_tuple))
            for relation_tuple in tuples
        ),
    )


def _assert_no_viewer_grant(
    client: ZanzibarClient,
    document: str = "document:doc",
) -> None:
    assert client.check(document, "viewer", "user:alice") is False
    assert client.list_objects("document", "viewer", "user:alice") == []

    expanded = client.expand(document, "viewer")
    assert expanded.users == set()
    assert expanded.usersets == set()


def test_schema_invalid_subject_set_tuple_is_ignored_by_read_apis() -> None:
    repo = InMemoryRelationRepository()
    _write_raw(
        repo,
        "group:eng#member@user:alice",
        "document:doc#viewer@group:eng#member",
    )

    client = _client(repo, _schema(_user_ref()))

    _assert_no_viewer_grant(client)


def test_schema_invalid_wildcard_tuple_is_ignored_by_read_apis() -> None:
    repo = InMemoryRelationRepository()
    _write_raw(repo, "document:doc#viewer@user:*")

    client = _client(repo, _schema(_user_ref()))

    _assert_no_viewer_grant(client)


def test_schema_unknown_reverse_bucket_tuples_are_ignored_by_read_apis() -> None:
    repo = InMemoryRelationRepository()
    _write_raw(
        repo,
        "legacy:doc#viewer@user:alice",
        "document:doc#legacy_viewer@user:alice",
    )

    client = _client(repo, _schema(_user_ref()))

    _assert_no_viewer_grant(client)


@pytest.mark.parametrize(
    ("schema_v1_subjects", "schema_v2_subjects", "tuples"),
    [
        (
            (_user_ref(),),
            (_group_member_ref(),),
            (("document:doc", "viewer", "user:alice"),),
        ),
        (
            (_group_member_ref(),),
            (_user_ref(),),
            (
                ("group:eng", "member", "user:alice"),
                ("document:doc", "viewer", "group:eng#member"),
            ),
        ),
        (
            (_user_wildcard_ref(),),
            (_user_ref(),),
            (("document:doc", "viewer", "user:*"),),
        ),
    ],
)
def test_schema_narrowing_invalidates_existing_direct_userset_and_wildcard_tuples(
    schema_v1_subjects: tuple[SubjectReference, ...],
    schema_v2_subjects: tuple[SubjectReference, ...],
    tuples: tuple[tuple[str, str, str], ...],
) -> None:
    repo = InMemoryRelationRepository()
    _client(repo, _schema(*schema_v1_subjects)).write_many(tuples)

    client_v2 = _client(repo, _schema(*schema_v2_subjects))

    _assert_no_viewer_grant(client_v2)


def test_tuple_to_userset_schema_narrowing_stops_following_old_parent_tuples() -> None:
    repo = InMemoryRelationRepository()
    client_v1 = _client(repo, _inherited_schema(SubjectReference(namespace="folder")))
    client_v1.write_many(
        (
            ("folder:f1", "viewer", "user:alice"),
            ("document:doc", "parent", "folder:f1"),
        )
    )

    client_v2 = _client(repo, _inherited_schema(SubjectReference(namespace="project")))

    assert client_v2.check("document:doc", "can_view", "user:alice") is False
    assert client_v2.list_objects("document", "can_view", "user:alice") == []

    expanded = client_v2.expand("document:doc", "can_view")
    assert expanded.users == set()
    assert expanded.usersets == set()
