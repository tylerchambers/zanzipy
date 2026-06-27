from zanzipy.client import ZanzibarClient
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository
from zanzipy.storage.revision import AtLeastAsFresh, Revision, WriteResult


def _registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "viewer",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
        )
    )
    return registry


def test_write_result_drives_at_least_as_fresh_check_and_reads() -> None:
    client = ZanzibarClient(
        relations_repository=InMemoryRelationRepository(),
        schema=_registry(),
    )

    write = client.write("document:doc1", "viewer", "user:alice")

    assert isinstance(write, WriteResult)
    assert write.revision == Revision(1)
    consistency = AtLeastAsFresh(write.token)
    assert client.check(
        "document:doc1",
        "viewer",
        "user:alice",
        consistency=consistency,
    )
    assert not client.check(
        "document:doc1",
        "viewer",
        "user:bob",
        consistency=consistency,
    )
    assert client.list_subjects_direct(
        "document:doc1",
        "viewer",
        consistency=consistency,
    ) == ["user:alice"]


def test_explicit_revision_reads_remain_stable_after_delete() -> None:
    client = ZanzibarClient(
        relations_repository=InMemoryRelationRepository(),
        schema=_registry(),
    )

    before = client.head_revision()
    write = client.write("document:doc1", "viewer", "user:alice")
    delete = client.delete("document:doc1", "viewer", "user:alice")

    assert not client.check_at_revision(
        "document:doc1",
        "viewer",
        "user:alice",
        revision=before,
    )
    assert client.check_at_revision(
        "document:doc1",
        "viewer",
        "user:alice",
        revision=write.revision,
    )
    assert not client.check_at_revision(
        "document:doc1",
        "viewer",
        "user:alice",
        revision=delete.revision,
    )
