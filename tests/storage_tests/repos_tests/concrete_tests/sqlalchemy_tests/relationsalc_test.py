from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from zanzipy.models.filter import TupleFilter
from zanzipy.models.tuple import RelationTuple
from zanzipy.storage.repos.concrete.sqlalchemy import (
    SQLAlchemyRelationRepository,
)


class TestSQLAlchemyRelationRepository:
    def test_write_read_delete_roundtrip(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

        repo = SQLAlchemyRelationRepository(SessionLocal)

        # Create schema (using repo's metadata)
        repo._metadata.create_all(bind=engine)  # type: ignore[attr-defined]

        t1 = RelationTuple.from_string("document:doc1#owner@user:alice")
        t2 = RelationTuple.from_string("document:doc1#owner@user:bob")
        t3 = RelationTuple.from_string("folder:f1#viewer@group:eng#member")

        repo.write(t1)
        repo.write_many([t2, t3])

        results = list(
            repo.read(
                TupleFilter(object_type="document", object_id="doc1", relation="owner")
            )
        )
        subjects = {str(t.subject) for t in results}
        assert subjects == {"user:alice", "user:bob"}

        rev_results = list(
            repo.read_reverse(TupleFilter(subject_type="user", subject_id="alice"))
        )
        assert any(str(t) == str(t1) for t in rev_results)

        assert repo.delete(t1) is True
        assert repo.delete(t1) is False
