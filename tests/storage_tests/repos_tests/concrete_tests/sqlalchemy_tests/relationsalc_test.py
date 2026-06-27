from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex

from zanzipy.models import RelationTuple, TupleFilter
from zanzipy.storage.repos.concrete.sqlalchemy import SQLAlchemyRelationRepository
from zanzipy.storage.revision import Revision, TupleMutation, WriteResult


def _repo() -> SQLAlchemyRelationRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repo = SQLAlchemyRelationRepository(session_factory)
    repo.create_schema(engine)
    return repo


def _postgresql_index_sql(
    repo: SQLAlchemyRelationRepository,
    name: str,
) -> str:
    index = next(index for index in repo._table.indexes if index.name == name)
    return str(CreateIndex(index).compile(dialect=postgresql.dialect()))


class TestSQLAlchemyRelationRepository:
    def test_write_delete_and_readd_create_snapshot_windows(self) -> None:
        repo = _repo()
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
        repo = _repo()
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
        ) == [owner, viewer]
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
        repo = _repo()
        t = RelationTuple.from_string("document:doc1#viewer@user:alice")

        write = repo.write((TupleMutation.touch(t),))
        delete = repo.write((TupleMutation.delete(t),))

        changes = list(repo.watch(after=Revision(0)))
        assert [change.revision for change in changes] == [
            write.revision,
            delete.revision,
        ]
        assert [change.relation_tuple for change in changes] == [t, t]

    def test_postgresql_indexes_match_mvcc_access_paths(self) -> None:
        repo = _repo()

        active_unique = _postgresql_index_sql(repo, "idx_rt_active_unique")
        assert "UNIQUE INDEX idx_rt_active_unique" in active_unique
        assert "WHERE deleted_revision IS NULL" in active_unique

        forward = _postgresql_index_sql(repo, "idx_rt_forward")
        assert "INCLUDE (subject_ns, subject_id, subject_rel)" in forward

        reverse = _postgresql_index_sql(repo, "idx_rt_reverse")
        assert "INCLUDE (object_ns, object_id, relation)" in reverse

        candidate = _postgresql_index_sql(repo, "idx_rt_object_type_relation")
        assert "(object_ns, relation, object_id)" in candidate

    def test_revision_columns_use_bigint_for_postgresql(self) -> None:
        repo = _repo()
        postgresql_dialect = postgresql.dialect()
        sqlite_dialect = sqlite.dialect()

        assert (
            repo._revisions.c.revision.type.compile(
                dialect=postgresql_dialect,
            )
            == "BIGINT"
        )
        assert (
            repo._table.c.created_revision.type.compile(
                dialect=postgresql_dialect,
            )
            == "BIGINT"
        )
        assert (
            repo._table.c.deleted_revision.type.compile(
                dialect=postgresql_dialect,
            )
            == "BIGINT"
        )
        assert (
            repo._revisions.c.revision.type.compile(
                dialect=sqlite_dialect,
            )
            == "INTEGER"
        )
