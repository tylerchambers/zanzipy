"""SQLAlchemy-backed revisioned relation tuple repository."""

from collections.abc import Callable  # noqa: TC003
from importlib import import_module
from typing import TYPE_CHECKING, cast

from zanzipy.storage.repos.abstract.relations import RelationRepository
from zanzipy.storage.repos.concrete._rows import (
    RELATION_TUPLE_COLUMNS,
    StoredRelationTuple,
    filter_values,
    stored_tuple_values,
)
from zanzipy.storage.revision import (
    RelationshipChange,
    RelationshipOperation,
    Revision,
    TupleMutation,
    WriteResult,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from sqlalchemy.engine import CursorResult, Engine
    from sqlalchemy.orm import Session

    from zanzipy.models import RelationTuple, TupleFilter

_TABLE_NAME = "relation_tuples"
_REVISIONS_TABLE_NAME = "revisions"


class SQLAlchemyRelationRepository(RelationRepository):
    """SQLAlchemy repository using created/deleted revisions for tuple visibility."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        """Import SQLAlchemy and define relation table metadata.

        The supplied ``session_factory`` is called per operation; sessions are
        committed or rolled back and then closed by repository methods.
        """
        try:
            self._sa = import_module("sqlalchemy")
        except Exception as exc:
            raise RuntimeError(
                "SQLAlchemy is required for SQLAlchemyRelationRepository. Install via "
                "pip install zanzipy[sqlalchemy]"
            ) from exc

        self._session_factory = session_factory
        self._metadata = self._sa.MetaData()
        self._revisions = self._sa.Table(
            _REVISIONS_TABLE_NAME,
            self._metadata,
            self._sa.Column(
                "revision",
                self._sa.Integer,
                primary_key=True,
                autoincrement=True,
            ),
            self._sa.Column(
                "created_at",
                self._sa.DateTime,
                nullable=False,
                server_default=self._sa.func.current_timestamp(),
            ),
        )
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
            self._sa.Column(
                "created_revision",
                self._sa.Integer,
                self._sa.ForeignKey(f"{_REVISIONS_TABLE_NAME}.revision"),
                primary_key=True,
            ),
            self._sa.Column(
                "deleted_revision",
                self._sa.Integer,
                self._sa.ForeignKey(f"{_REVISIONS_TABLE_NAME}.revision"),
                nullable=True,
            ),
            self._sa.Index(
                "idx_rt_forward",
                "object_ns",
                "object_id",
                "relation",
                "created_revision",
                "deleted_revision",
            ),
            self._sa.Index(
                "idx_rt_reverse",
                "subject_ns",
                "subject_id",
                "subject_rel",
                "created_revision",
                "deleted_revision",
            ),
            self._sa.Index(
                "idx_rt_active_unique",
                "tuple_key",
                unique=True,
                sqlite_where=self._sa.text("deleted_revision IS NULL"),
            ),
        )

    def create_schema(self, bind: Engine) -> None:
        """Create revision and relation tuple tables plus indexes on ``bind``."""

        self._metadata.create_all(bind=bind)

    def write(self, mutations: Iterable[TupleMutation]) -> WriteResult:
        """Persist idempotent mutations atomically using a short-lived session.

        Raises:
            ValueError: If a mutation contains an unknown operation.
        """
        session = self._session_factory()
        revision: Revision | None = None
        changes: list[tuple[RelationTuple, RelationshipOperation]] = []
        try:
            for mutation in mutations:
                if mutation.operation is RelationshipOperation.WRITE:
                    if self._active_tuple(session, mutation.relation_tuple) is not None:
                        continue
                    revision = self._ensure_write_revision(session, revision)
                    session.execute(
                        self._table.insert().values(
                            **stored_tuple_values(
                                mutation.relation_tuple,
                                created_revision=revision.value,
                            )
                        )
                    )
                    changes.append((mutation.relation_tuple, mutation.operation))
                    continue

                if mutation.operation is RelationshipOperation.DELETE:
                    active = self._active_tuple(session, mutation.relation_tuple)
                    if active is None:
                        continue
                    revision = self._ensure_write_revision(session, revision)
                    session.execute(
                        self._table.update()
                        .where(self._table.c.tuple_key == str(mutation.relation_tuple))
                        .where(self._table.c.deleted_revision.is_(None))
                        .values(deleted_revision=revision.value)
                    )
                    changes.append((active, mutation.operation))
                    continue

                raise ValueError(
                    f"unknown tuple mutation operation: {mutation.operation}"
                )
            if revision is None:
                session.rollback()
                return WriteResult(self.head_revision())
            session.commit()
            return WriteResult(revision)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def head_revision(self) -> Revision:
        """Return the greatest committed revision visible through a session."""
        session = self._session_factory()
        try:
            return self._head_revision(session)
        finally:
            session.close()

    def get(
        self,
        key: RelationTuple,
        *,
        revision: Revision,
    ) -> RelationTuple | None:
        """Return ``key`` when its visibility window includes ``revision``.

        Raises:
            ValueError: If ``revision`` is newer than the head revision.
        """
        self._raise_if_future_revision(revision)
        session = self._session_factory()
        try:
            row = (
                session.execute(
                    self._sa.select(self._table)
                    .where(self._table.c.tuple_key == str(key))
                    .where(self._visible_at(revision))
                )
                .mappings()
                .fetchone()
            )
            if row is None:
                return None
            return StoredRelationTuple.from_mapping(row).to_tuple()
        finally:
            session.close()

    def read(
        self,
        filter: TupleFilter,
        *,
        revision: Revision,
    ) -> Iterable[RelationTuple]:
        """Return tuples visible at ``revision`` that match ``filter``.

        Raises:
            ValueError: If ``revision`` is newer than the head revision.
        """
        return self._select(filter, revision=revision)

    def read_reverse(
        self,
        filter: TupleFilter,
        *,
        revision: Revision,
    ) -> Iterable[RelationTuple]:
        """Return reverse-filtered tuples visible at ``revision``.

        Raises:
            ValueError: If ``revision`` is newer than the head revision.
        """
        return self._select(filter, revision=revision)

    def watch(self, *, after: Revision) -> Iterator[RelationshipChange]:
        """Yield writes and deletes committed after ``after`` by revision order.

        Raises:
            ValueError: If ``after`` is newer than the head revision.
        """
        self._raise_if_future_revision(after)
        session = self._session_factory()
        try:
            created = (
                session.execute(
                    self._sa.select(
                        self._table,
                        self._table.c.created_revision.label("change_revision"),
                    ).where(self._table.c.created_revision > after.value)
                )
                .mappings()
                .all()
            )
            deleted = (
                session.execute(
                    self._sa.select(
                        self._table,
                        self._table.c.deleted_revision.label("change_revision"),
                    )
                    .where(self._table.c.deleted_revision.is_not(None))
                    .where(self._table.c.deleted_revision > after.value)
                )
                .mappings()
                .all()
            )
            changes = [
                (
                    int(row["change_revision"]),
                    RelationshipChange(
                        revision=Revision(int(row["change_revision"])),
                        relation_tuple=StoredRelationTuple.from_mapping(row).to_tuple(),
                        operation=RelationshipOperation.WRITE,
                    ),
                )
                for row in created
            ]
            changes.extend(
                (
                    int(row["change_revision"]),
                    RelationshipChange(
                        revision=Revision(int(row["change_revision"])),
                        relation_tuple=StoredRelationTuple.from_mapping(row).to_tuple(),
                        operation=RelationshipOperation.DELETE,
                    ),
                )
                for row in deleted
            )
            for _, change in sorted(changes, key=lambda item: item[0]):
                yield change
        finally:
            session.close()

    def info(self) -> dict[str, object]:
        """Return SQLAlchemy backend diagnostics, table names, and columns."""
        return {
            "backend": "sqlalchemy",
            "table": _TABLE_NAME,
            "revisions_table": _REVISIONS_TABLE_NAME,
            "head_revision": self.head_revision().value,
            "columns": RELATION_TUPLE_COLUMNS,
        }

    def _select(
        self,
        filter: TupleFilter,
        *,
        revision: Revision,
    ) -> list[RelationTuple]:
        self._raise_if_future_revision(revision)
        session = self._session_factory()
        try:
            stmt = self._sa.select(self._table).where(self._visible_at(revision))
            conditions = [
                self._table.c[column] == value
                for column, value in filter_values(filter)
            ]
            if conditions:
                stmt = stmt.where(self._sa.and_(*conditions))
            stmt = stmt.order_by(
                self._table.c.tuple_key,
                self._table.c.created_revision,
            )
            rows = session.execute(stmt).mappings().all()
            return [StoredRelationTuple.from_mapping(row).to_tuple() for row in rows]
        finally:
            session.close()

    def _active_tuple(
        self,
        session: Session,
        key: RelationTuple,
    ) -> RelationTuple | None:
        row = (
            session.execute(
                self._sa.select(self._table)
                .where(self._table.c.tuple_key == str(key))
                .where(self._table.c.deleted_revision.is_(None))
            )
            .mappings()
            .fetchone()
        )
        if row is None:
            return None
        return StoredRelationTuple.from_mapping(row).to_tuple()

    def _ensure_write_revision(
        self,
        session: Session,
        revision: Revision | None,
    ) -> Revision:
        if revision is not None:
            return revision
        result = cast(
            "CursorResult[object]",
            session.execute(self._revisions.insert().values()),
        )
        return Revision(int(result.inserted_primary_key[0]))

    def _head_revision(self, session: Session) -> Revision:
        revision = self._revisions.c.revision
        value = session.execute(
            self._sa.select(self._sa.func.coalesce(self._sa.func.max(revision), 0))
        ).scalar_one()
        return Revision(int(value))

    def _visible_at(self, revision: Revision) -> object:
        return self._sa.and_(
            self._table.c.created_revision <= revision.value,
            self._sa.or_(
                self._table.c.deleted_revision.is_(None),
                self._table.c.deleted_revision > revision.value,
            ),
        )

    def _raise_if_future_revision(self, revision: Revision) -> None:
        head = self.head_revision()
        if revision > head:
            raise ValueError(f"requested revision {revision} is newer than head {head}")
