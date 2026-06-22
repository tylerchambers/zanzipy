from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from zanzipy.models.filter import TupleFilter
from zanzipy.models.tuple import RelationTuple
from zanzipy.storage.repos.concrete.sqlalchemy import SQLAlchemyRelationRepository


def _repo() -> SQLAlchemyRelationRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repo = SQLAlchemyRelationRepository(session_factory)
    repo.create_schema(engine)
    return repo


class TestSQLAlchemyRelationRepository:
    def test_write_read_get_delete_roundtrip(self) -> None:
        repo = _repo()

        t1 = RelationTuple.from_string("document:doc1#owner@user:alice")
        t2 = RelationTuple.from_string("document:doc1#owner@user:bob")
        t3 = RelationTuple.from_string("folder:f1#viewer@group:eng#member")

        repo.write(t1)
        repo.write_many([t2, t3])

        assert repo.get(t1) == t1
        assert repo.exists(t1)

        results = list(
            repo.read(
                TupleFilter(object_type="document", object_id="doc1", relation="owner")
            )
        )
        assert {str(t.subject) for t in results} == {"user:alice", "user:bob"}

        rev_results = list(
            repo.read_reverse(TupleFilter(subject_type="user", subject_id="alice"))
        )
        assert rev_results == [t1]

        assert repo.delete_by_key(t1) is True
        assert repo.delete_by_key(t1) is False
        assert repo.get(t1) is None

    def test_upsert_is_idempotent_for_direct_subjects(self) -> None:
        repo = _repo()
        direct = RelationTuple.from_string("document:doc1#viewer@user:alice")
        userset = RelationTuple.from_string("document:doc1#viewer@group:eng#member")

        repo.write_many([direct, direct, userset, userset])

        assert set(repo.read(TupleFilter())) == {direct, userset}

    def test_reverse_read_applies_all_filter_fields(self) -> None:
        repo = _repo()
        viewer = RelationTuple.from_string("document:doc1#viewer@user:alice")
        owner = RelationTuple.from_string("document:doc2#owner@user:alice")
        other = RelationTuple.from_string("document:doc3#viewer@user:bob")
        repo.write_many([viewer, owner, other])

        results = list(
            repo.read_reverse(
                TupleFilter(
                    subject_type="user",
                    subject_id="alice",
                    relation="viewer",
                )
            )
        )
        assert results == [viewer]
