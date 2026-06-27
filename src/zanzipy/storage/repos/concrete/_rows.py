"""Storage-row helpers shared by concrete relation repositories."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from zanzipy.models import Obj, Relation, RelationTuple, Subject

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from zanzipy.models import TupleFilter

SUBJECT_RELATION_NONE = ""

LOGICAL_RELATION_TUPLE_COLUMNS = (
    "tuple_key",
    "object_ns",
    "object_id",
    "relation",
    "subject_ns",
    "subject_id",
    "subject_rel",
)
RELATION_TUPLE_COLUMNS = (
    *LOGICAL_RELATION_TUPLE_COLUMNS,
    "created_revision",
    "deleted_revision",
)

_FILTER_COLUMNS = (
    ("object_type", "object_ns"),
    ("object_id", "object_id"),
    ("relation", "relation"),
    ("subject_type", "subject_ns"),
    ("subject_id", "subject_id"),
    ("subject_relation", "subject_rel"),
)


@dataclass(frozen=True, slots=True)
class StoredRelationTuple:
    """Database representation of a relation tuple and its visibility window."""

    tuple_key: str
    object_ns: str
    object_id: str
    relation: str
    subject_ns: str
    subject_id: str
    subject_rel: str
    created_revision: int | None = None
    deleted_revision: int | None = None

    @classmethod
    def from_tuple(
        cls,
        tuple_: RelationTuple,
        *,
        created_revision: int | None = None,
        deleted_revision: int | None = None,
    ) -> StoredRelationTuple:
        """Build a storage row for ``tuple_`` with optional visibility bounds."""
        return cls(
            tuple_key=str(tuple_),
            object_ns=str(tuple_.object.namespace),
            object_id=str(tuple_.object.id),
            relation=str(tuple_.relation),
            subject_ns=str(tuple_.subject.namespace),
            subject_id=str(tuple_.subject.id),
            subject_rel=(
                SUBJECT_RELATION_NONE
                if tuple_.subject.relation is None
                else str(tuple_.subject.relation)
            ),
            created_revision=created_revision,
            deleted_revision=deleted_revision,
        )

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> StoredRelationTuple:
        """Build a storage row from SQL-style column mappings."""
        subject_rel = row["subject_rel"]
        return cls(
            tuple_key=str(row["tuple_key"]),
            object_ns=str(row["object_ns"]),
            object_id=str(row["object_id"]),
            relation=str(row["relation"]),
            subject_ns=str(row["subject_ns"]),
            subject_id=str(row["subject_id"]),
            subject_rel=(
                SUBJECT_RELATION_NONE if subject_rel is None else str(subject_rel)
            ),
            created_revision=_optional_int(_mapping_value(row, "created_revision")),
            deleted_revision=_optional_int(_mapping_value(row, "deleted_revision")),
        )

    def as_values(self) -> dict[str, str | int | None]:
        """Return column values suitable for SQL parameter binding."""

        return {
            "tuple_key": self.tuple_key,
            "object_ns": self.object_ns,
            "object_id": self.object_id,
            "relation": self.relation,
            "subject_ns": self.subject_ns,
            "subject_id": self.subject_id,
            "subject_rel": self.subject_rel,
            "created_revision": self.created_revision,
            "deleted_revision": self.deleted_revision,
        }

    def to_tuple(self) -> RelationTuple:
        """Return the public ``RelationTuple`` represented by this row."""

        return RelationTuple(
            object=Obj.from_parts(self.object_ns, self.object_id),
            relation=Relation(self.relation),
            subject=Subject.from_parts(
                self.subject_ns,
                self.subject_id,
                None if self.subject_rel == SUBJECT_RELATION_NONE else self.subject_rel,
            ),
        )


def stored_tuple_values(
    tuple_: RelationTuple,
    *,
    created_revision: int,
    deleted_revision: int | None = None,
) -> dict[str, str | int | None]:
    """Return storage values for ``tuple_`` at its visibility window."""

    return StoredRelationTuple.from_tuple(
        tuple_,
        created_revision=created_revision,
        deleted_revision=deleted_revision,
    ).as_values()


def unique_stored_tuples(tuples: Iterable[RelationTuple]) -> list[StoredRelationTuple]:
    """Return stored rows de-duplicated by canonical tuple key in input order."""

    rows: dict[str, StoredRelationTuple] = {}
    for tuple_ in tuples:
        row = StoredRelationTuple.from_tuple(tuple_)
        rows.setdefault(row.tuple_key, row)
    return list(rows.values())


def filter_values(filter: TupleFilter) -> list[tuple[str, str]]:
    """Return storage column comparisons for the populated filter fields."""

    values: list[tuple[str, str]] = []
    for attr_name, column_name in _FILTER_COLUMNS:
        value = getattr(filter, attr_name)
        if value is None:
            continue
        values.append((column_name, value))
    return values


def _mapping_value(row: Mapping[str, object], key: str) -> object:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)
