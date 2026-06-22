from zanzipy.models.tuple import RelationTuple
from zanzipy.storage.repos.concrete.memory.relations import (
    InMemoryRelationRepository,
    MemoryTupleFilter,
)


class TestInMemoryRelationRepository:
    def test_write_and_get_and_exists(self) -> None:
        repo = InMemoryRelationRepository()
        t = RelationTuple.from_string("document:doc1#owner@user:alice")

        repo.write(t)
        assert repo.get(t) == t
        assert repo.exists(t)

    def test_delete_and_delete_by_key(self) -> None:
        repo = InMemoryRelationRepository()
        t = RelationTuple.from_string("document:doc1#viewer@user:bob")
        repo.write(t)

        assert repo.delete(t) is True
        assert repo.delete_by_key(t) is False
        assert repo.get(t) is None

    def test_find_forward_by_object_and_relation(self) -> None:
        repo = InMemoryRelationRepository()
        t1 = RelationTuple.from_string("document:doc1#viewer@user:bob")
        t2 = RelationTuple.from_string("document:doc1#owner@user:alice")
        t3 = RelationTuple.from_string("document:doc2#viewer@user:bob")
        repo.write_many([t1, t2, t3])

        results = list(
            repo.read(MemoryTupleFilter(object_type="document", object_id="doc1"))
        )
        assert set(results) == {t1, t2}

        results = list(repo.read(MemoryTupleFilter(relation="viewer")))
        assert set(results) == {t1, t3}

    def test_find_reverse_by_subject(self) -> None:
        repo = InMemoryRelationRepository()
        t1 = RelationTuple.from_string("document:doc1#viewer@user:bob")
        t2 = RelationTuple.from_string("document:doc2#owner@user:bob")
        t3 = RelationTuple.from_string("document:doc3#viewer@user:alice")
        repo.write_many([t1, t2, t3])

        results = list(
            repo.read_reverse(MemoryTupleFilter(subject_type="user", subject_id="bob"))
        )
        assert set(results) == {t1, t2}

    def test_delete_where(self) -> None:
        repo = InMemoryRelationRepository()
        t1 = RelationTuple.from_string("document:doc1#viewer@user:bob")
        t2 = RelationTuple.from_string("document:doc1#owner@user:alice")
        t3 = RelationTuple.from_string("document:doc2#viewer@user:bob")
        repo.write_many([t1, t2, t3])

        deleted = repo.delete_where(MemoryTupleFilter(object_id="doc1"))
        assert deleted == 2
        assert set(repo.read(MemoryTupleFilter())) == {t3}
