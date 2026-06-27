from zanzipy.models import RelationTuple, TupleFilter
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository
from zanzipy.storage.revision import Revision, TupleMutation, WriteResult


class TestInMemoryRelationRepository:
    def test_write_delete_and_readd_create_snapshot_windows(self) -> None:
        repo = InMemoryRelationRepository()
        t = RelationTuple.from_string("document:doc1#viewer@user:alice")

        initial = repo.head_revision()
        write = repo.write((TupleMutation.touch(t),))
        noop = repo.write((TupleMutation.touch(t),))
        delete = repo.write((TupleMutation.delete(t),))
        readd = repo.write((TupleMutation.touch(t),))

        assert isinstance(write, WriteResult)
        assert initial == Revision(0)
        assert write.revision == Revision(1)
        assert noop.revision == write.revision
        assert delete.revision == Revision(2)
        assert readd.revision == Revision(3)
        assert repo.head_revision() == readd.revision

        assert repo.get(t, revision=initial) is None
        assert repo.get(t, revision=write.revision) == t
        assert repo.get(t, revision=delete.revision) is None
        assert repo.get(t, revision=readd.revision) == t

    def test_read_and_read_reverse_respect_revision(self) -> None:
        repo = InMemoryRelationRepository()
        viewer = RelationTuple.from_string("document:doc1#viewer@user:alice")
        owner = RelationTuple.from_string("document:doc1#owner@user:bob")

        initial = repo.head_revision()
        write = repo.write((TupleMutation.touch(viewer), TupleMutation.touch(owner)))
        delete = repo.write((TupleMutation.delete(viewer),))

        assert list(repo.read(TupleFilter(), revision=initial)) == []
        assert list(
            repo.read(
                TupleFilter(object_type="document", object_id="doc1"),
                revision=write.revision,
            )
        ) == [viewer, owner]
        assert list(
            repo.read_reverse(
                TupleFilter(subject_type="user", subject_id="alice"),
                revision=write.revision,
            )
        ) == [viewer]
        assert (
            list(
                repo.read_reverse(
                    TupleFilter(subject_type="user", subject_id="alice"),
                    revision=delete.revision,
                )
            )
            == []
        )
        assert list(repo.by_object(viewer.object, revision=delete.revision)) == [owner]

    def test_watch_returns_committed_changes_after_revision(self) -> None:
        repo = InMemoryRelationRepository()
        t = RelationTuple.from_string("document:doc1#viewer@user:alice")

        write = repo.write((TupleMutation.touch(t),))
        delete = repo.write((TupleMutation.delete(t),))

        changes = list(repo.watch(after=Revision(0)))
        assert [change.revision for change in changes] == [
            write.revision,
            delete.revision,
        ]
        assert [change.relation_tuple for change in changes] == [t, t]
