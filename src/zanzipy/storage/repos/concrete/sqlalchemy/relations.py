import contextlib
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

from zanzipy.models.filter import TupleFilter
from zanzipy.models.tuple import RelationTuple
from zanzipy.storage.repos.abstract.relations import RelationRepository

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sqlalchemy.orm import Session, sessionmaker


_TABLE_NAME = "relation_tuples"


@dataclass(frozen=True, slots=True)
class _Key:
    object_ns: str
    object_id: str
    relation: str
    subject_ns: str
    subject_id: str
    subject_rel: str | None


class SQLAlchemyRelationRepository(RelationRepository[RelationTuple, TupleFilter]):
    """SQLAlchemy-backed repository.

    Accepts either a Session, a sessionmaker, or a callable returning a Session.
    Does not manage transaction scope; caller owns session lifecycle.
    """

    def __init__(
        self,
        session_or_factory: Session | sessionmaker | Callable[[], Session],
    ) -> None:
        # Lazy import to keep optional dependency out of core import path
        try:
            sa = import_module("sqlalchemy")
            orm = import_module("sqlalchemy.orm")
        except Exception as exc:
            raise RuntimeError(
                "SQLAlchemy is required for SQLAlchemyRelationRepository. Install via "
                "pip install zanzipy[sqlalchemy]"
            ) from exc

        if isinstance(session_or_factory, orm.Session):
            self._session_factory = lambda: session_or_factory
        elif isinstance(session_or_factory, orm.sessionmaker):
            self._session_factory = session_or_factory
        else:
            self._session_factory = session_or_factory

        # Build metadata and table
        self._sa = sa
        self._metadata = sa.MetaData()
        self._table = sa.Table(
            _TABLE_NAME,
            self._metadata,
            sa.Column("object_ns", sa.String, primary_key=True),
            sa.Column("object_id", sa.String, primary_key=True),
            sa.Column("relation", sa.String, primary_key=True),
            sa.Column("subject_ns", sa.String, primary_key=True),
            sa.Column("subject_id", sa.String, primary_key=True),
            sa.Column("subject_rel", sa.String, primary_key=True, nullable=True),
        )

    def key_of(self, entity: RelationTuple) -> _Key:
        return _Key(
            object_ns=str(entity.object.namespace),
            object_id=str(entity.object.id),
            relation=str(entity.relation),
            subject_ns=str(entity.subject.namespace),
            subject_id=str(entity.subject.id),
            subject_rel=(
                None
                if entity.subject.relation is None
                else str(entity.subject.relation)
            ),
        )

    def upsert(self, entity: RelationTuple) -> None:
        k = self.key_of(entity)
        sess = self._session_factory()
        insert_stmt = (
            self._table.insert()
            .values(
                object_ns=k.object_ns,
                object_id=k.object_id,
                relation=k.relation,
                subject_ns=k.subject_ns,
                subject_id=k.subject_id,
                subject_rel=k.subject_rel,
            )
            .prefix_with("OR IGNORE")
        )
        try:
            sess.execute(insert_stmt)
            sess.flush()
            with contextlib.suppress(Exception):
                sess.commit()
        except Exception:
            # Fallback: ignore duplicate insert errors
            with contextlib.suppress(Exception):
                sess.rollback()

    def delete_by_key(self, key: _Key) -> bool:
        sess = self._session_factory()
        result = sess.execute(
            self._table.delete().where(
                self._sa.and_(
                    self._table.c.object_ns == key.object_ns,
                    self._table.c.object_id == key.object_id,
                    self._table.c.relation == key.relation,
                    self._table.c.subject_ns == key.subject_ns,
                    self._table.c.subject_id == key.subject_id,
                    self._table.c.subject_rel.is_(key.subject_rel),
                )
            )
        )
        sess.flush()
        with contextlib.suppress(Exception):
            sess.commit()
        return bool(result.rowcount and result.rowcount > 0)

    def get(self, key: _Key) -> RelationTuple | None:
        sess = self._session_factory()
        row = sess.execute(
            self._sa.select(self._table).where(
                self._sa.and_(
                    self._table.c.object_ns == key.object_ns,
                    self._table.c.object_id == key.object_id,
                    self._table.c.relation == key.relation,
                    self._table.c.subject_ns == key.subject_ns,
                    self._table.c.subject_id == key.subject_id,
                    self._table.c.subject_rel.is_(key.subject_rel),
                )
            )
        ).fetchone()
        if row is None:
            return None
        m = row._mapping
        tuple_str = (
            f"{m['object_ns']}:{m['object_id']}#{m['relation']}@{m['subject_ns']}:{m['subject_id']}"
            + (f"#{m['subject_rel']}" if m["subject_rel"] is not None else "")
        )
        return RelationTuple.from_string(tuple_str)

    def find(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        sess = self._session_factory()
        conditions: list[object] = []
        if filter.object_type is not None:
            conditions.append(self._table.c.object_ns == filter.object_type)
        if filter.object_id is not None:
            conditions.append(self._table.c.object_id == filter.object_id)
        if filter.relation is not None:
            conditions.append(self._table.c.relation == filter.relation)
        if filter.subject_type is not None:
            conditions.append(self._table.c.subject_ns == filter.subject_type)
        if filter.subject_id is not None:
            conditions.append(self._table.c.subject_id == filter.subject_id)
        if filter.subject_relation is not None:
            conditions.append(self._table.c.subject_rel == filter.subject_relation)

        stmt = self._sa.select(self._table)
        if conditions:
            stmt = stmt.where(self._sa.and_(*conditions))
        for row in sess.execute(stmt):
            m = row._mapping
            tuple_str = (
                f"{m['object_ns']}:{m['object_id']}#{m['relation']}@{m['subject_ns']}:{m['subject_id']}"
                + (f"#{m['subject_rel']}" if m["subject_rel"] is not None else "")
            )
            yield RelationTuple.from_string(tuple_str)

    def read(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        return self.find(filter)

    def read_reverse(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        sess = self._session_factory()
        conditions = []
        if filter.subject_type is not None:
            conditions.append(self._table.c.subject_ns == filter.subject_type)
        if filter.subject_id is not None:
            conditions.append(self._table.c.subject_id == filter.subject_id)
        if filter.subject_relation is not None:
            conditions.append(self._table.c.subject_rel == filter.subject_relation)
        stmt = self._sa.select(self._table)
        if conditions:
            stmt = stmt.where(self._sa.and_(*conditions))
        for row in sess.execute(stmt):
            m = row._mapping
            tuple_str = (
                f"{m['object_ns']}:{m['object_id']}#{m['relation']}@{m['subject_ns']}:{m['subject_id']}"
                + (f"#{m['subject_rel']}" if m["subject_rel"] is not None else "")
            )
            yield RelationTuple.from_string(tuple_str)
