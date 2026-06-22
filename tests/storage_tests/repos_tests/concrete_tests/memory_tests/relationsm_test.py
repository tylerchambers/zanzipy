from zanzipy.models import RelationTuple, TupleFilter
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository


class TestInMemoryRelationRepository:
    def test_write_and_get_and_exists(self) -> None:
        repo = InMemoryRelationRepository()
        t = RelationTuple.from_string("document:doc1#owner@user:alice")

        repo.write(t)
        repo.write(t)

        assert repo.get(t) == t
        assert repo.exists(t)
        assert list(repo.read(TupleFilter())) == [t]

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

        results = list(repo.read(TupleFilter(object_type="document", object_id="doc1")))
        assert results == [t1, t2]

        results = list(repo.read(TupleFilter(relation="viewer")))
        assert results == [t1, t3]

    def test_reverse_read_applies_all_filter_fields(self) -> None:
        repo = InMemoryRelationRepository()
        t1 = RelationTuple.from_string("document:doc1#viewer@user:bob")
        t2 = RelationTuple.from_string("document:doc2#owner@user:bob")
        t3 = RelationTuple.from_string("document:doc3#viewer@user:alice")
        repo.write_many([t1, t2, t3])

        results = list(
            repo.read_reverse(
                TupleFilter(
                    subject_type="user",
                    subject_id="bob",
                    relation="viewer",
                )
            )
        )
        assert results == [t1]

    def test_reverse_read_can_match_direct_subject_exactly(self) -> None:
        repo = InMemoryRelationRepository()
        direct = RelationTuple.from_string("document:doc1#viewer@group:eng")
        userset = RelationTuple.from_string("document:doc2#viewer@group:eng#member")
        repo.write_many([direct, userset])

        assert list(repo.read_reverse(TupleFilter.from_subject(direct.subject))) == [
            direct
        ]
        assert list(
            repo.read_reverse(TupleFilter(subject_type="group", subject_id="eng"))
        ) == [
            direct,
            userset,
        ]

    def test_delete_where(self) -> None:
        repo = InMemoryRelationRepository()
        t1 = RelationTuple.from_string("document:doc1#viewer@user:bob")
        t2 = RelationTuple.from_string("document:doc1#owner@user:alice")
        t3 = RelationTuple.from_string("document:doc2#viewer@user:bob")
        repo.write_many([t1, t2, t3])

        deleted = repo.delete_where(TupleFilter(object_id="doc1"))
        assert deleted == 2
        assert list(repo.read(TupleFilter())) == [t3]
