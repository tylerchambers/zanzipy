"""SQLite-backed tenant-scoped revisioned relation tuple repository."""

from contextlib import suppress
import sqlite3
from typing import TYPE_CHECKING

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
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from zanzipy.models import RelationTuple, TupleFilter

_SELECT_COLUMNS = ", ".join(RELATION_TUPLE_COLUMNS)
_INSERT_COLUMNS = ", ".join(RELATION_TUPLE_COLUMNS)
_INSERT_PLACEHOLDERS = ", ".join(f":{column}" for column in RELATION_TUPLE_COLUMNS)
_VISIBLE_AT = (
    "tenant_id = ? "
    "AND created_revision <= ? "
    "AND (deleted_revision IS NULL OR deleted_revision > ?)"
)


class SQLiteRelationRepository(RelationRepository):
    """SQLite repository using tenant-scoped created/deleted revisions."""

    def __init__(self, db_path: str = ":memory:") -> None:
        """Open the database, enable SQLite storage pragmas, and create schema."""
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON;")
        if db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL;")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS revisions (
                    tenant_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tenant_id, revision)
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS relation_tuples (
                    tenant_id TEXT NOT NULL,
                    tuple_key TEXT NOT NULL,
                    object_ns TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    subject_ns TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    subject_rel TEXT NOT NULL,
                    created_revision INTEGER NOT NULL,
                    deleted_revision INTEGER NULL,
                    PRIMARY KEY (tenant_id, tuple_key, created_revision),
                    FOREIGN KEY (tenant_id, created_revision)
                        REFERENCES revisions(tenant_id, revision),
                    FOREIGN KEY (tenant_id, deleted_revision)
                        REFERENCES revisions(tenant_id, revision)
                );
                """
            )
            self._conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_rt_active_unique
                ON relation_tuples (tenant_id, tuple_key)
                WHERE deleted_revision IS NULL;
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rt_forward
                ON relation_tuples (
                    tenant_id,
                    object_ns,
                    object_id,
                    relation,
                    created_revision,
                    deleted_revision
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rt_reverse
                ON relation_tuples (
                    tenant_id,
                    subject_ns,
                    subject_id,
                    subject_rel,
                    created_revision,
                    deleted_revision
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rt_object_type_relation
                ON relation_tuples (tenant_id, object_ns, relation, object_id);
                """
            )

    def write(
        self,
        context: WriteContext,
        mutations: Iterable[TupleMutation],
    ) -> WriteResult:
        """Persist idempotent tenant mutations atomically in one revision.

        Raises:
            ValueError: If a mutation contains an unknown operation.
        """
        revision: Revision | None = None
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            for mutation in mutations:
                if mutation.operation is RelationshipOperation.WRITE:
                    if self._active_tuple(context.tenant, mutation.relation_tuple):
                        continue
                    revision = self._ensure_write_revision(context.tenant, revision)
                    self._insert_tuple(
                        context.tenant, mutation.relation_tuple, revision
                    )
                    continue

                if mutation.operation is RelationshipOperation.DELETE:
                    active = self._active_tuple(context.tenant, mutation.relation_tuple)
                    if active is None:
                        continue
                    revision = self._ensure_write_revision(context.tenant, revision)
                    self._mark_tuple_deleted(
                        context.tenant,
                        mutation.relation_tuple,
                        revision,
                    )
                    continue

                raise ValueError(
                    f"unknown tuple mutation operation: {mutation.operation}"
                )
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        committed = self.head_revision(context.tenant) if revision is None else revision
        return WriteResult(RevisionToken(context.tenant, committed))

    def head_revision(self, tenant: TenantId) -> Revision:
        """Return the greatest committed revision stored for ``tenant``."""
        value = self._conn.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM revisions WHERE tenant_id = ?",
            (str(tenant),),
        ).fetchone()[0]
        return Revision(int(value))

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
        row = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM relation_tuples
            WHERE tuple_key = ? AND {_VISIBLE_AT}
            """,
            (
                str(key),
                str(context.tenant),
                context.revision.value,
                context.revision.value,
            ),
        ).fetchone()
        if row is None:
            return None
        return StoredRelationTuple.from_mapping(row).to_tuple()

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
        tenant_id = str(tenant)
        created = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}, created_revision AS change_revision
            FROM relation_tuples
            WHERE tenant_id = ? AND created_revision > ?
            """,
            (tenant_id, after.value),
        ).fetchall()
        deleted = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}, deleted_revision AS change_revision
            FROM relation_tuples
            WHERE tenant_id = ?
              AND deleted_revision IS NOT NULL
              AND deleted_revision > ?
            """,
            (tenant_id, after.value),
        ).fetchall()
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

    def info(self) -> dict[str, object]:
        """Return SQLite backend diagnostics and stored row column names."""
        rows = self._conn.execute(
            """
            SELECT tenant_id, MAX(revision) AS revision
            FROM revisions
            GROUP BY tenant_id
            """
        ).fetchall()
        return {
            "backend": "sqlite",
            "head_revisions": {
                str(row["tenant_id"]): int(row["revision"]) for row in rows
            },
            "columns": RELATION_TUPLE_COLUMNS,
        }

    def close(self) -> None:
        """Close the owned SQLite connection, suppressing close failures."""
        with suppress(Exception):
            self._conn.close()

    def _select(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> list[RelationTuple]:
        self._raise_if_future_revision(context)
        clauses = [_VISIBLE_AT]
        params: list[str | int] = [
            str(context.tenant),
            context.revision.value,
            context.revision.value,
        ]
        for column, value in filter_values(filter):
            clauses.append(f"{column} = ?")
            params.append(value)

        cursor = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM relation_tuples
            WHERE {" AND ".join(clauses)}
            ORDER BY tuple_key, created_revision
            """,
            params,
        )
        return [StoredRelationTuple.from_mapping(row).to_tuple() for row in cursor]

    def _active_tuple(
        self,
        tenant: TenantId,
        relation_tuple: RelationTuple,
    ) -> RelationTuple | None:
        row = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM relation_tuples
            WHERE tenant_id = ? AND tuple_key = ? AND deleted_revision IS NULL
            """,
            (str(tenant), str(relation_tuple)),
        ).fetchone()
        if row is None:
            return None
        return StoredRelationTuple.from_mapping(row).to_tuple()

    def _ensure_write_revision(
        self,
        tenant: TenantId,
        revision: Revision | None,
    ) -> Revision:
        if revision is not None:
            return revision
        tenant_id = str(tenant)
        next_revision = int(
            self._conn.execute(
                """
                SELECT COALESCE(MAX(revision), 0) + 1
                FROM revisions
                WHERE tenant_id = ?
                """,
                (tenant_id,),
            ).fetchone()[0]
        )
        self._conn.execute(
            "INSERT INTO revisions (tenant_id, revision) VALUES (?, ?)",
            (tenant_id, next_revision),
        )
        return Revision(next_revision)

    def _insert_tuple(
        self,
        tenant: TenantId,
        relation_tuple: RelationTuple,
        revision: Revision,
    ) -> None:
        self._conn.execute(
            f"""
            INSERT INTO relation_tuples ({_INSERT_COLUMNS})
            VALUES ({_INSERT_PLACEHOLDERS})
            """,
            stored_tuple_values(
                relation_tuple,
                tenant_id=str(tenant),
                created_revision=revision.value,
            ),
        )

    def _mark_tuple_deleted(
        self,
        tenant: TenantId,
        relation_tuple: RelationTuple,
        revision: Revision,
    ) -> None:
        self._conn.execute(
            """
            UPDATE relation_tuples
            SET deleted_revision = ?
            WHERE tenant_id = ? AND tuple_key = ? AND deleted_revision IS NULL
            """,
            (revision.value, str(tenant), str(relation_tuple)),
        )

    def _raise_if_future_revision(self, context: ReadContext) -> None:
        head = self.head_revision(context.tenant)
        if context.revision > head:
            raise ValueError(
                f"requested revision {context.revision} is newer than head {head}"
            )
