"""SQLite-backed relation tuple repository."""

from contextlib import suppress
import sqlite3
from typing import TYPE_CHECKING

from zanzipy.storage.repos.abstract.relations import RelationRepository
from zanzipy.storage.repos.concrete._rows import (
    RELATION_TUPLE_COLUMNS,
    StoredRelationTuple,
    filter_values,
    unique_stored_tuples,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from zanzipy.models.filter import TupleFilter
    from zanzipy.models.tuple import RelationTuple

_SELECT_COLUMNS = ", ".join(RELATION_TUPLE_COLUMNS)
_INSERT_COLUMNS = ", ".join(RELATION_TUPLE_COLUMNS)
_INSERT_PLACEHOLDERS = ", ".join(f":{column}" for column in RELATION_TUPLE_COLUMNS)


class SQLiteRelationRepository(RelationRepository):
    """Stdlib SQLite implementation of ``RelationRepository``.

    The schema stores the tuple's canonical string as the primary key and stores
    absent subject relations as a non-null sentinel. That avoids SQLite's
    nullable-composite-primary-key behavior and keeps upserts truly idempotent
    for direct subjects.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
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
                CREATE TABLE IF NOT EXISTS relation_tuples (
                    tuple_key TEXT PRIMARY KEY,
                    object_ns TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    subject_ns TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    subject_rel TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rt_object
                ON relation_tuples (object_ns, object_id, relation);
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rt_subject
                ON relation_tuples (subject_ns, subject_id, subject_rel);
                """
            )

    def upsert(self, entity: RelationTuple) -> None:
        row = StoredRelationTuple.from_tuple(entity)
        with self._conn:
            self._conn.execute(
                f"""
                INSERT INTO relation_tuples ({_INSERT_COLUMNS})
                VALUES ({_INSERT_PLACEHOLDERS})
                ON CONFLICT(tuple_key) DO NOTHING;
                """,
                row.as_values(),
            )

    def upsert_many(self, entities: Iterable[RelationTuple]) -> None:
        rows = unique_stored_tuples(entities)
        if not rows:
            return None
        with self._conn:
            self._conn.executemany(
                f"""
                INSERT INTO relation_tuples ({_INSERT_COLUMNS})
                VALUES ({_INSERT_PLACEHOLDERS})
                ON CONFLICT(tuple_key) DO NOTHING;
                """,
                [row.as_values() for row in rows],
            )
        return None

    def delete_by_key(self, key: RelationTuple) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM relation_tuples WHERE tuple_key = ?",
                (str(key),),
            )
        return cursor.rowcount > 0

    def delete_many_by_key(self, keys: Iterable[RelationTuple]) -> int:
        tuple_keys = list(dict.fromkeys(str(key) for key in keys))
        deleted = 0
        with self._conn:
            for tuple_key in tuple_keys:
                cursor = self._conn.execute(
                    "DELETE FROM relation_tuples WHERE tuple_key = ?",
                    (tuple_key,),
                )
                deleted += int(cursor.rowcount > 0)
        return deleted

    def get(self, key: RelationTuple) -> RelationTuple | None:
        row = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM relation_tuples
            WHERE tuple_key = ?
            """,
            (str(key),),
        ).fetchone()
        if row is None:
            return None
        return StoredRelationTuple.from_mapping(row).to_tuple()

    def read(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        return self._select(filter)

    def read_reverse(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        return self._select(filter)

    def _select(self, filter: TupleFilter) -> list[RelationTuple]:
        clauses: list[str] = []
        params: list[str] = []
        for column, value in filter_values(filter):
            clauses.append(f"{column} = ?")
            params.append(value)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = self._conn.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM relation_tuples
            {where}
            ORDER BY tuple_key
            """,
            params,
        )
        return [StoredRelationTuple.from_mapping(row).to_tuple() for row in cursor]

    def close(self) -> None:
        with suppress(Exception):
            self._conn.close()
