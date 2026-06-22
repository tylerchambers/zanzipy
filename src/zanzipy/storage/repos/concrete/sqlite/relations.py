from contextlib import suppress
from dataclasses import dataclass
import sqlite3
from typing import TYPE_CHECKING

from zanzipy.models.filter import TupleFilter
from zanzipy.models.tuple import RelationTuple
from zanzipy.storage.repos.abstract.relations import RelationRepository

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True, slots=True)
class _Key:
    object_ns: str
    object_id: str
    relation: str
    subject_ns: str
    subject_id: str
    subject_rel: str | None


class SQLiteRelationRepository(RelationRepository[RelationTuple, TupleFilter]):
    """SQLite-backed RelationRepository using stdlib sqlite3.

    - No external dependencies; safe to ship as an optional extra.
    - File DB path or ":memory:" supported.
    - Creates schema and indices on first use.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(self._db_path, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS relation_tuples (
                object_ns TEXT NOT NULL,
                object_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                subject_ns TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                subject_rel TEXT,
                PRIMARY KEY (
                    object_ns, object_id, relation,
                    subject_ns, subject_id, subject_rel
                )
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
        self._conn.execute(
            """
            INSERT INTO relation_tuples (
                object_ns, object_id, relation,
                subject_ns, subject_id, subject_rel
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(object_ns, object_id, relation,
                        subject_ns, subject_id, subject_rel)
            DO NOTHING;
            """,
            (
                k.object_ns,
                k.object_id,
                k.relation,
                k.subject_ns,
                k.subject_id,
                k.subject_rel,
            ),
        )

    def delete_by_key(self, key: _Key) -> bool:
        cur = self._conn.execute(
            """
            DELETE FROM relation_tuples WHERE
                object_ns = ? AND object_id = ? AND relation = ? AND
                subject_ns = ? AND subject_id = ? AND subject_rel IS ?
            """,
            (
                key.object_ns,
                key.object_id,
                key.relation,
                key.subject_ns,
                key.subject_id,
                key.subject_rel,
            ),
        )
        return cur.rowcount > 0

    def get(self, key: _Key) -> RelationTuple | None:
        row = self._conn.execute(
            """
            SELECT object_ns, object_id, relation,
                   subject_ns, subject_id, subject_rel
            FROM relation_tuples
            WHERE object_ns = ? AND object_id = ? AND relation = ? AND
                  subject_ns = ? AND subject_id = ? AND subject_rel IS ?
            """,
            (
                key.object_ns,
                key.object_id,
                key.relation,
                key.subject_ns,
                key.subject_id,
                key.subject_rel,
            ),
        ).fetchone()
        if row is None:
            return None
        return RelationTuple.from_string(
            f"{row[0]}:{row[1]}#{row[2]}@{row[3]}:{row[4]}"
            + (f"#{row[5]}" if row[5] is not None else "")
        )

    def find(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        clauses: list[str] = []
        params: list[object] = []
        if filter.object_type is not None:
            clauses.append("object_ns = ?")
            params.append(filter.object_type)
        if filter.object_id is not None:
            clauses.append("object_id = ?")
            params.append(filter.object_id)
        if filter.relation is not None:
            clauses.append("relation = ?")
            params.append(filter.relation)
        if filter.subject_type is not None:
            clauses.append("subject_ns = ?")
            params.append(filter.subject_type)
        if filter.subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(filter.subject_id)
        if filter.subject_relation is not None:
            clauses.append("subject_rel = ?")
            params.append(filter.subject_relation)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT object_ns, object_id, relation, subject_ns, "
            "subject_id, subject_rel "
            "FROM relation_tuples" + where
        )
        for row in self._conn.execute(sql, tuple(params)):
            yield RelationTuple.from_string(
                f"{row[0]}:{row[1]}#{row[2]}@{row[3]}:{row[4]}"
                + (f"#{row[5]}" if row[5] is not None else "")
            )

    def read(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        return self.find(filter)

    def read_reverse(self, filter: TupleFilter) -> Iterable[RelationTuple]:
        clauses: list[str] = []
        params: list[object] = []
        if filter.subject_type is not None:
            clauses.append("subject_ns = ?")
            params.append(filter.subject_type)
        if filter.subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(filter.subject_id)
        if filter.subject_relation is not None:
            clauses.append("subject_rel = ?")
            params.append(filter.subject_relation)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT object_ns, object_id, relation, subject_ns, subject_id,"
            " subject_rel "
            "FROM relation_tuples" + where
        )
        for row in self._conn.execute(sql, tuple(params)):
            yield RelationTuple.from_string(
                f"{row[0]}:{row[1]}#{row[2]}@{row[3]}:{row[4]}"
                + (f"#{row[5]}" if row[5] is not None else "")
            )

    def close(self) -> None:
        with suppress(Exception):
            self._conn.close()
