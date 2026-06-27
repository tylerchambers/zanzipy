"""SQLite-backed revisioned relation tuple repository."""

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
    RelationshipChange,
    RelationshipOperation,
    Revision,
    TupleMutation,
    WriteResult,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from zanzipy.models import RelationTuple, TupleFilter

_SELECT_COLUMNS = ", ".join(RELATION_TUPLE_COLUMNS)
_INSERT_COLUMNS = ", ".join(RELATION_TUPLE_COLUMNS)
_INSERT_PLACEHOLDERS = ", ".join(f":{column}" for column in RELATION_TUPLE_COLUMNS)
_VISIBLE_AT = (
    "created_revision <= ? AND (deleted_revision IS NULL OR deleted_revision > ?)"
)


class SQLiteRelationRepository(RelationRepository):
    """SQLite repository using created/deleted revisions for tuple visibility."""

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
                    revision INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS relation_tuples (
                    tuple_key TEXT NOT NULL,
                    object_ns TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    subject_ns TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    subject_rel TEXT NOT NULL,
                    created_revision INTEGER NOT NULL,
                    deleted_revision INTEGER NULL,
                    PRIMARY KEY (tuple_key, created_revision),
                    FOREIGN KEY (created_revision) REFERENCES revisions(revision),
                    FOREIGN KEY (deleted_revision) REFERENCES revisions(revision)
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rt_forward
                ON relation_tuples (
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
                CREATE UNIQUE INDEX IF NOT EXISTS idx_rt_active_unique
                ON relation_tuples (tuple_key)
                WHERE deleted_revision IS NULL;
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rt_object_type_relation
                ON relation_tuples (object_ns, relation, object_id);
                """
            )

    def write(self, mutations: Iterable[TupleMutation]) -> WriteResult:
        """Persist idempotent mutations atomically in one revision transaction.

        Raises:
            ValueError: If a mutation contains an unknown operation.
        """
        revision: Revision | None = None
        changes: list[tuple[RelationTuple, RelationshipOperation]] = []
        with self._conn:
            for mutation in mutations:
                if mutation.operation is RelationshipOperation.WRITE:
                    if self._active_tuple(mutation.relation_tuple) is not None:
                        continue
                    revision = self._ensure_write_revision(revision)
                    self._insert_tuple(mutation.relation_tuple, revision)
                    changes.append((mutation.relation_tuple, mutation.operation))
                    continue

                if mutation.operation is RelationshipOperation.DELETE:
                    active = self._active_tuple(mutation.relation_tuple)
                    if active is None:
                        continue
                    revision = self._ensure_write_revision(revision)
                    self._mark_tuple_deleted(mutation.relation_tuple, revision)
                    changes.append((active, mutation.operation))
                    continue

                raise ValueError(
                    f"unknown tuple mutation operation: {mutation.operation}"
                )
        return WriteResult(self.head_revision() if revision is None else revision)

    def head_revision(self) -> Revision:
        """Return the greatest committed revision stored in SQLite."""
        value = self._conn.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM revisions"
        ).fetchone()[0]
        return Revision(int(value))

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
        row = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM relation_tuples
            WHERE tuple_key = ? AND {_VISIBLE_AT}
            """,
            (str(key), revision.value, revision.value),
        ).fetchone()
        if row is None:
            return None
        return StoredRelationTuple.from_mapping(row).to_tuple()

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
        created = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}, created_revision AS change_revision
            FROM relation_tuples
            WHERE created_revision > ?
            """,
            (after.value,),
        ).fetchall()
        deleted = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}, deleted_revision AS change_revision
            FROM relation_tuples
            WHERE deleted_revision IS NOT NULL AND deleted_revision > ?
            """,
            (after.value,),
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
        return {
            "backend": "sqlite",
            "head_revision": self.head_revision().value,
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
        revision: Revision,
    ) -> list[RelationTuple]:
        self._raise_if_future_revision(revision)
        clauses = [_VISIBLE_AT]
        params: list[str | int] = [revision.value, revision.value]
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

    def _active_tuple(self, relation_tuple: RelationTuple) -> RelationTuple | None:
        row = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM relation_tuples
            WHERE tuple_key = ? AND deleted_revision IS NULL
            """,
            (str(relation_tuple),),
        ).fetchone()
        if row is None:
            return None
        return StoredRelationTuple.from_mapping(row).to_tuple()

    def _ensure_write_revision(self, revision: Revision | None) -> Revision:
        if revision is not None:
            return revision
        cursor = self._conn.execute("INSERT INTO revisions DEFAULT VALUES")
        return Revision(int(cursor.lastrowid))

    def _insert_tuple(self, relation_tuple: RelationTuple, revision: Revision) -> None:
        self._conn.execute(
            f"""
            INSERT INTO relation_tuples ({_INSERT_COLUMNS})
            VALUES ({_INSERT_PLACEHOLDERS})
            """,
            stored_tuple_values(relation_tuple, created_revision=revision.value),
        )

    def _mark_tuple_deleted(
        self,
        relation_tuple: RelationTuple,
        revision: Revision,
    ) -> None:
        self._conn.execute(
            """
            UPDATE relation_tuples
            SET deleted_revision = ?
            WHERE tuple_key = ? AND deleted_revision IS NULL
            """,
            (revision.value, str(relation_tuple)),
        )

    def _raise_if_future_revision(self, revision: Revision) -> None:
        head = self.head_revision()
        if revision > head:
            raise ValueError(f"requested revision {revision} is newer than head {head}")
