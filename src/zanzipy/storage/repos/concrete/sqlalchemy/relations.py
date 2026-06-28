"""SQLAlchemy-backed tenant-scoped revisioned relation tuple repository."""

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
    ReadContext,
    RelationshipChange,
    RelationshipOperation,
    Revision,
    RevisionToken,
    TenantId,
    TupleMutation,
    WriteContext,
    WriteResult,
    validated_mutation_batch,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from sqlalchemy.engine import CursorResult, Engine
    from sqlalchemy.orm import Session

    from zanzipy.models import RelationTuple, TupleFilter

_TABLE_NAME = "relation_tuples"
_REVISIONS_TABLE_NAME = "revisions"


class SQLAlchemyRelationRepository(RelationRepository):
    """SQLAlchemy repository using tenant-scoped created/deleted revisions."""

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
        revision_type = self._sa.BigInteger().with_variant(self._sa.Integer(), "sqlite")
        self._revisions = self._sa.Table(
            _REVISIONS_TABLE_NAME,
            self._metadata,
            self._sa.Column("tenant_id", self._sa.String, primary_key=True),
            self._sa.Column("revision", revision_type, primary_key=True),
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
            self._sa.Column("tenant_id", self._sa.String, primary_key=True),
            self._sa.Column("tuple_key", self._sa.String, primary_key=True),
            self._sa.Column("object_ns", self._sa.String, nullable=False),
            self._sa.Column("object_id", self._sa.String, nullable=False),
            self._sa.Column("relation", self._sa.String, nullable=False),
            self._sa.Column("subject_ns", self._sa.String, nullable=False),
            self._sa.Column("subject_id", self._sa.String, nullable=False),
            self._sa.Column("subject_rel", self._sa.String, nullable=False),
            self._sa.Column("created_revision", revision_type, primary_key=True),
            self._sa.Column("deleted_revision", revision_type, nullable=True),
            self._sa.ForeignKeyConstraint(
                ("tenant_id", "created_revision"),
                (
                    f"{_REVISIONS_TABLE_NAME}.tenant_id",
                    f"{_REVISIONS_TABLE_NAME}.revision",
                ),
            ),
            self._sa.ForeignKeyConstraint(
                ("tenant_id", "deleted_revision"),
                (
                    f"{_REVISIONS_TABLE_NAME}.tenant_id",
                    f"{_REVISIONS_TABLE_NAME}.revision",
                ),
            ),
            self._sa.Index(
                "idx_rt_active_unique",
                "tenant_id",
                "tuple_key",
                unique=True,
                sqlite_where=self._sa.text("deleted_revision IS NULL"),
                postgresql_where=self._sa.text("deleted_revision IS NULL"),
            ),
            self._sa.Index(
                "idx_rt_forward",
                "tenant_id",
                "object_ns",
                "object_id",
                "relation",
                "created_revision",
                "deleted_revision",
                postgresql_include=("subject_ns", "subject_id", "subject_rel"),
            ),
            self._sa.Index(
                "idx_rt_reverse",
                "tenant_id",
                "subject_ns",
                "subject_id",
                "subject_rel",
                "created_revision",
                "deleted_revision",
                postgresql_include=("object_ns", "object_id", "relation"),
            ),
            self._sa.Index(
                "idx_rt_object_type_relation",
                "tenant_id",
                "object_ns",
                "relation",
                "object_id",
            ),
        )

    def create_schema(self, bind: Engine) -> None:
        """Create revision and relation tuple tables plus indexes on ``bind``."""

        self._metadata.create_all(bind=bind)

    def write(
        self,
        context: WriteContext,
        mutations: Iterable[TupleMutation],
    ) -> WriteResult:
        """Persist idempotent tenant mutations atomically in one revision.

        Transient same-tenant revision allocation conflicts are retried so
        concurrent writers receive distinct tenant-local revisions when the
        database surfaces a primary-key or serialization conflict.

        Raises:
            ValueError: If a mutation contains an unknown operation.
        """
        retry_errors = (self._sa.exc.IntegrityError, self._sa.exc.OperationalError)
        mutations = validated_mutation_batch(mutations)
        for attempt in range(3):
            session = self._session_factory()
            revision: Revision | None = None
            try:
                for mutation in mutations:
                    if mutation.operation is RelationshipOperation.WRITE:
                        if (
                            self._active_tuple(
                                session,
                                context.tenant,
                                mutation.relation_tuple,
                            )
                            is not None
                        ):
                            continue
                        revision = self._ensure_write_revision(
                            session,
                            context.tenant,
                            revision,
                        )
                        session.execute(
                            self._table.insert().values(
                                **stored_tuple_values(
                                    mutation.relation_tuple,
                                    tenant_id=str(context.tenant),
                                    created_revision=revision.value,
                                )
                            )
                        )
                        continue

                    if mutation.operation is RelationshipOperation.DELETE:
                        active = self._active_tuple(
                            session,
                            context.tenant,
                            mutation.relation_tuple,
                        )
                        if active is None:
                            continue
                        revision = self._ensure_write_revision(
                            session,
                            context.tenant,
                            revision,
                        )
                        session.execute(
                            self._table.update()
                            .where(self._table.c.tenant_id == str(context.tenant))
                            .where(
                                self._table.c.tuple_key == str(mutation.relation_tuple)
                            )
                            .where(self._table.c.deleted_revision.is_(None))
                            .values(deleted_revision=revision.value)
                        )
                        continue

                    raise ValueError(
                        f"unknown tuple mutation operation: {mutation.operation}"
                    )
                if revision is None:
                    session.rollback()
                    return WriteResult(
                        RevisionToken(
                            context.tenant,
                            self.head_revision(context.tenant),
                        )
                    )
                session.commit()
                return WriteResult(RevisionToken(context.tenant, revision))
            except retry_errors:
                session.rollback()
                if attempt == 2:
                    raise
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        raise RuntimeError("failed to allocate tenant revision")

    def head_revision(self, tenant: TenantId) -> Revision:
        """Return the greatest committed revision visible for ``tenant``."""
        session = self._session_factory()
        try:
            return self._head_revision(session, tenant)
        finally:
            session.close()

    def get(
        self,
        key: RelationTuple,
        *,
        context: ReadContext,
    ) -> RelationTuple | None:
        """Return ``key`` when its tenant visibility window includes context.

        Raises:
            ValueError: If ``context.revision`` is newer than the tenant head.
        """
        self._raise_if_future_revision(context)
        session = self._session_factory()
        try:
            row = (
                session.execute(
                    self._sa.select(self._table)
                    .where(self._table.c.tenant_id == str(context.tenant))
                    .where(self._table.c.tuple_key == str(key))
                    .where(self._visible_at(context))
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
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Return tuples visible in ``context`` that match ``filter``.

        Raises:
            ValueError: If ``context.revision`` is newer than the tenant head.
        """
        return self._select(filter, context=context)

    def read_reverse(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Return reverse-filtered tuples visible in ``context``.

        Raises:
            ValueError: If ``context.revision`` is newer than the tenant head.
        """
        return self._select(filter, context=context)

    def watch(
        self,
        tenant: TenantId,
        *,
        after: Revision,
    ) -> Iterator[RelationshipChange]:
        """Yield ``tenant`` writes and deletes committed after ``after``.

        Raises:
            ValueError: If ``after`` is newer than the tenant head revision.
        """
        self._raise_if_future_revision(ReadContext(tenant, after))
        session = self._session_factory()
        try:
            created = (
                session.execute(
                    self._sa.select(
                        self._table,
                        self._table.c.created_revision.label("change_revision"),
                    )
                    .where(self._table.c.tenant_id == str(tenant))
                    .where(self._table.c.created_revision > after.value)
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
                    .where(self._table.c.tenant_id == str(tenant))
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
                        token=RevisionToken(
                            tenant, Revision(int(row["change_revision"]))
                        ),
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
                        token=RevisionToken(
                            tenant, Revision(int(row["change_revision"]))
                        ),
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
        session = self._session_factory()
        try:
            rows = (
                session.execute(
                    self._sa.select(
                        self._revisions.c.tenant_id,
                        self._sa.func.max(self._revisions.c.revision).label("revision"),
                    ).group_by(self._revisions.c.tenant_id)
                )
                .mappings()
                .all()
            )
        finally:
            session.close()
        return {
            "backend": "sqlalchemy",
            "table": _TABLE_NAME,
            "revisions_table": _REVISIONS_TABLE_NAME,
            "head_revisions": {
                str(row["tenant_id"]): int(row["revision"]) for row in rows
            },
            "columns": RELATION_TUPLE_COLUMNS,
        }

    def _select(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> list[RelationTuple]:
        self._raise_if_future_revision(context)
        session = self._session_factory()
        try:
            stmt = self._sa.select(self._table).where(self._visible_at(context))
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
        tenant: TenantId,
        key: RelationTuple,
    ) -> RelationTuple | None:
        row = (
            session.execute(
                self._sa.select(self._table)
                .where(self._table.c.tenant_id == str(tenant))
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
        tenant: TenantId,
        revision: Revision | None,
    ) -> Revision:
        if revision is not None:
            return revision
        tenant_id = str(tenant)
        next_revision = int(
            session.execute(
                self._sa.select(
                    self._sa.func.coalesce(
                        self._sa.func.max(self._revisions.c.revision),
                        0,
                    )
                    + 1
                ).where(self._revisions.c.tenant_id == tenant_id)
            ).scalar_one()
        )
        result = cast(
            "CursorResult[object]",
            session.execute(
                self._revisions.insert().values(
                    tenant_id=tenant_id,
                    revision=next_revision,
                )
            ),
        )
        if result.rowcount != 1:
            raise RuntimeError("failed to create relation repository revision")
        return Revision(next_revision)

    def _head_revision(self, session: Session, tenant: TenantId) -> Revision:
        value = session.execute(
            self._sa.select(
                self._sa.func.coalesce(self._sa.func.max(self._revisions.c.revision), 0)
            ).where(self._revisions.c.tenant_id == str(tenant))
        ).scalar_one()
        return Revision(int(value))

    def _visible_at(self, context: ReadContext) -> object:
        return self._sa.and_(
            self._table.c.tenant_id == str(context.tenant),
            self._table.c.created_revision <= context.revision.value,
            self._sa.or_(
                self._table.c.deleted_revision.is_(None),
                self._table.c.deleted_revision > context.revision.value,
            ),
        )

    def _raise_if_future_revision(self, context: ReadContext) -> None:
        head = self.head_revision(context.tenant)
        if context.revision > head:
            raise ValueError(
                f"requested revision {context.revision} is newer than head {head}"
            )
