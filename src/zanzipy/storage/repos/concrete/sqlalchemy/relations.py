"""SQLAlchemy-backed relation tuple repository."""

from collections.abc import Callable  # noqa: TC003
from importlib import import_module
from typing import TYPE_CHECKING

from zanzipy.storage.repos.abstract.relations import RelationRepository
from zanzipy.storage.repos.concrete._rows import (
    RELATION_TUPLE_COLUMNS,
    StoredRelationTuple,
    filter_values,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.engine import Engine

    from zanzipy.models.filter import TupleFilter
    from zanzipy.models.tuple import RelationTuple

_TABLE_NAME = "relation_tuples"


class SQLAlchemyRelationRepository(RelationRepository):
    """SQLAlchemy implementation of ``RelationRepository``.

    Pass a zero-argument ``session_factory`` such as ``sessionmaker(...)``. The
    repository opens, commits or rolls back, and closes one session per write;
    reads materialize their result before closing the session. The table uses a
    non-null canonical tuple key for idempotency across SQL dialects.
    """

    def __init__(self, session_factory: Callable[[], object]) -> None:
        try:
            self._sa = import_module("sqlalchemy")
        except Exception as exc:
            raise RuntimeError(
                "SQLAlchemy is required for SQLAlchemyRelationRepository. Install via "
                "pip install zanzipy[sqlalchemy]"
            ) from exc

        self._session_factory = session_factory
        self._metadata = self._sa.MetaData()
        self._table = self._sa.Table(
            _TABLE_NAME,
            self._metadata,
            self._sa.Column("tuple_key", self._sa.String, primary_key=True),
            self._sa.Column("object_ns", self._sa.String, nullable=False),
            self._sa.Column("object_id", self._sa.String, nullable=False),
            self._sa.Column("relation", self._sa.String, nullable=False),
            self._sa.Column("subject_ns", self._sa.String, nullable=False),
            self._sa.Column("subject_id", self._sa.String, nullable=False),
            self._sa.Column("subject_rel", self._sa.String, nullable=False),
            self._sa.Index(
                "idx_rt_object",
                "object_ns",
                "object_id",
                "relation",
            ),
            self._sa.Index(
                "idx_rt_subject",
                "subject_ns",
                "subject_id",
                "subject_rel",
            ),
        )

    def create_schema(self, bind: Engine) -> None:
        """Create the relation tuple table and indexes on ``bind``."""

        self._metadata.create_all(bind=bind)

    def upsert(self, entity: RelationTuple) -> None:
        values = StoredRelationTuple.from_tuple(entity).as_values()
        session = self._session_factory()
        try:
            session.execute(self._table.insert().values(**values))
            session.commit()
        except self._sa.exc.IntegrityError:
            session.rollback()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_by_key(self, key: RelationTuple) -> bool:
        session = self._session_factory()
        try:
            result = session.execute(
                self._table.delete().where(self._table.c.tuple_key == str(key))
            )
            session.commit()
            return bool(result.rowcount and result.rowcount > 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get(self, key: RelationTuple) -> RelationTuple | None:
        session = self._session_factory()
        try:
            row = (
                session.execute(
                    self._sa.select(self._table).where(
                        self._table.c.tuple_key == str(key)
                    )
                )
                .mappings()
                .fetchone()
            )
            if row is None:
                return None
            return StoredRelationTuple.from_mapping(row).to_tuple()
        finally:
            session.close()

    def read(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        return self._select(filter)

    def read_reverse(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        return self._select(filter)

    def _select(self, filter: TupleFilter) -> list[RelationTuple]:
        session = self._session_factory()
        try:
            stmt = self._sa.select(self._table)
            conditions = [
                self._table.c[column] == value
                for column, value in filter_values(filter)
            ]
            if conditions:
                stmt = stmt.where(self._sa.and_(*conditions))
            stmt = stmt.order_by(self._table.c.tuple_key)
            rows = session.execute(stmt).mappings().all()
            return [StoredRelationTuple.from_mapping(row).to_tuple() for row in rows]
        finally:
            session.close()

    def info(self) -> dict[str, object]:
        return {
            "backend": "sqlalchemy",
            "table": _TABLE_NAME,
            "columns": RELATION_TUPLE_COLUMNS,
        }
