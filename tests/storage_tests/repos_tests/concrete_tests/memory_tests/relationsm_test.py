import pytest

from zanzipy.models import RelationTuple, TupleFilter
from zanzipy.storage.repos.concrete.memory.relations import (
    InMemoryRelationRepository,
    delete,
    touch,
)
from zanzipy.storage.revision import (
    ReadContext,
    Revision,
    TenantId,
    TupleMutation,
    WriteContext,
    WriteResult,
)

TENANT = TenantId("default")
OTHER_TENANT = TenantId("other")


def _read_context(revision: Revision, tenant: TenantId = TENANT) -> ReadContext:
    return ReadContext(tenant, revision)


class TestInMemoryRelationRepository:
    def test_write_delete_and_readd_create_snapshot_windows(self) -> None:
        repo = InMemoryRelationRepository()
        t = RelationTuple.from_string("document:doc1#viewer@user:alice")

        initial = repo.head_revision(TENANT)
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(t),))
        noop = repo.write(WriteContext(TENANT), (TupleMutation.touch(t),))
        delete = repo.write(WriteContext(TENANT), (TupleMutation.delete(t),))
        noop_delete = repo.write(WriteContext(TENANT), (TupleMutation.delete(t),))
        readd = repo.write(WriteContext(TENANT), (TupleMutation.touch(t),))

        assert isinstance(write, WriteResult)
        assert initial == Revision(0)
        assert write.revision == Revision(1)
        assert noop.revision == write.revision
        assert delete.revision == Revision(2)
        assert noop_delete.revision == delete.revision
        assert readd.revision == Revision(3)
        assert repo.head_revision(TENANT) == readd.revision

        assert repo.get(t, context=_read_context(initial)) is None
        assert repo.get(t, context=_read_context(write.revision)) == t
        assert repo.get(t, context=_read_context(delete.revision)) is None
        assert repo.get(t, context=_read_context(readd.revision)) == t

    def test_read_and_read_reverse_respect_revision(self) -> None:
        repo = InMemoryRelationRepository()
        viewer = RelationTuple.from_string("document:doc1#viewer@user:alice")
        owner = RelationTuple.from_string("document:doc1#owner@user:bob")

        initial = repo.head_revision(TENANT)
        write = repo.write(
            WriteContext(TENANT),
            (TupleMutation.touch(viewer), TupleMutation.touch(owner)),
        )
        delete = repo.write(WriteContext(TENANT), (TupleMutation.delete(viewer),))

        assert list(repo.read(TupleFilter(), context=_read_context(initial))) == []
        assert list(
            repo.read(
                TupleFilter(object_type="document", object_id="doc1"),
                context=_read_context(write.revision),
            )
        ) == [viewer, owner]
        assert list(
            repo.read_reverse(
                TupleFilter(subject_type="user", subject_id="alice"),
                context=_read_context(write.revision),
            )
        ) == [viewer]
        assert (
            list(
                repo.read_reverse(
                    TupleFilter(subject_type="user", subject_id="alice"),
                    context=_read_context(delete.revision),
                )
            )
            == []
        )
        assert list(
            repo.by_object(viewer.object, context=_read_context(delete.revision))
        ) == [owner]

    def test_watch_returns_committed_changes_after_revision(self) -> None:
        repo = InMemoryRelationRepository()
        t = RelationTuple.from_string("document:doc1#viewer@user:alice")

        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(t),))
        delete = repo.write(WriteContext(TENANT), (TupleMutation.delete(t),))

        changes = list(repo.watch(TENANT, after=Revision(0)))
        assert [change.token for change in changes] == [
            write.token,
            delete.token,
        ]
        assert [change.tenant for change in changes] == [TENANT, TENANT]
        assert [change.relation_tuple for change in changes] == [t, t]

    def test_tenants_have_isolated_tuple_state_and_revision_sequences(self) -> None:
        repo = InMemoryRelationRepository()
        t = RelationTuple.from_string("document:doc1#viewer@user:alice")

        first_write = repo.write(WriteContext(TENANT), (TupleMutation.touch(t),))
        other_write = repo.write(WriteContext(OTHER_TENANT), (TupleMutation.touch(t),))

        assert first_write.tenant == TENANT
        assert other_write.tenant == OTHER_TENANT
        assert first_write.revision == Revision(1)
        assert other_write.revision == Revision(1)
        assert repo.get(t, context=_read_context(first_write.revision, TENANT)) == t
        assert (
            repo.get(t, context=_read_context(other_write.revision, OTHER_TENANT)) == t
        )

        delete = repo.write(WriteContext(TENANT), (TupleMutation.delete(t),))

        assert delete.revision == Revision(2)
        assert repo.head_revision(TENANT) == Revision(2)
        assert repo.head_revision(OTHER_TENANT) == Revision(1)
        assert repo.get(t, context=_read_context(delete.revision, TENANT)) is None
        assert (
            repo.get(t, context=_read_context(other_write.revision, OTHER_TENANT)) == t
        )

    def test_new_tenant_empty_and_future_revisions_are_tenant_scoped(self) -> None:
        repo = InMemoryRelationRepository()
        t = RelationTuple.from_string("document:doc1#viewer@user:alice")

        other_write = repo.write(WriteContext(OTHER_TENANT), (TupleMutation.touch(t),))

        assert other_write.revision == Revision(1)
        assert repo.head_revision(TENANT) == Revision(0)
        assert list(repo.read(TupleFilter(), context=_read_context(Revision(0)))) == []
        with pytest.raises(ValueError, match="newer than head"):
            list(repo.read(TupleFilter(), context=_read_context(Revision(1))))

    def test_watch_is_tenant_scoped(self) -> None:
        repo = InMemoryRelationRepository()
        tenant_tuple = RelationTuple.from_string("document:doc1#viewer@user:alice")
        other_tuple = RelationTuple.from_string("document:doc2#viewer@user:bob")

        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(tenant_tuple),))
        other_write = repo.write(
            WriteContext(OTHER_TENANT),
            (TupleMutation.touch(other_tuple),),
        )
        delete = repo.write(WriteContext(TENANT), (TupleMutation.delete(tenant_tuple),))

        tenant_changes = list(repo.watch(TENANT, after=Revision(0)))
        other_changes = list(repo.watch(OTHER_TENANT, after=Revision(0)))

        assert [change.token for change in tenant_changes] == [
            write.token,
            delete.token,
        ]
        assert [change.tenant for change in tenant_changes] == [TENANT, TENANT]
        assert [change.relation_tuple for change in tenant_changes] == [
            tenant_tuple,
            tenant_tuple,
        ]
        assert [change.token for change in other_changes] == [other_write.token]
        assert [change.tenant for change in other_changes] == [OTHER_TENANT]
        assert [change.relation_tuple for change in other_changes] == [other_tuple]

    def test_invalid_mutation_rolls_back_partial_write(self) -> None:
        repo = InMemoryRelationRepository()
        t = RelationTuple.from_string("document:doc1#viewer@user:alice")
        bad = TupleMutation(t, "invalid")  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="unknown tuple mutation"):
            repo.write(WriteContext(TENANT), (TupleMutation.touch(t), bad))

        assert repo.head_revision(TENANT) == Revision(0)
        assert repo.get(t, context=_read_context(Revision(0))) is None
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(t),))
        assert write.revision == Revision(1)
        assert repo.get(t, context=_read_context(write.revision)) == t

    def test_empty_write_initializes_tenant_without_advancing_revision(self) -> None:
        repo = InMemoryRelationRepository()

        result = repo.write(WriteContext(TENANT), ())

        assert result.revision == Revision(0)
        assert repo.head_revision(TENANT) == Revision(0)
        assert list(repo.watch(TENANT, after=Revision(0))) == []
        assert repo.info() == {
            "backend": "memory",
            "head_revisions": {"default": 0},
            "tuples": 0,
        }

    def test_watch_rejects_future_revision_for_empty_tenant(self) -> None:
        repo = InMemoryRelationRepository()

        with pytest.raises(ValueError, match="newer than head"):
            list(repo.watch(TENANT, after=Revision(1)))

    def test_unknown_stored_snapshot_revision_is_rejected(self) -> None:
        repo = InMemoryRelationRepository()
        first = RelationTuple.from_string("document:doc1#viewer@user:alice")
        second = RelationTuple.from_string("document:doc2#viewer@user:bob")
        repo.write(WriteContext(TENANT), (TupleMutation.touch(first),))
        repo.write(WriteContext(TENANT), (TupleMutation.touch(second),))
        del repo._snapshots[str(TENANT)][1]

        with pytest.raises(ValueError, match="unknown relation repository revision 1"):
            list(repo.read(TupleFilter(), context=_read_context(Revision(1))))

    def test_touch_and_delete_helpers_create_tuple_mutations(self) -> None:
        tuple_ = RelationTuple.from_string("document:doc1#viewer@user:alice")

        assert touch(tuple_) == TupleMutation.touch(tuple_)
        assert delete(tuple_) == TupleMutation.delete(tuple_)
