"""Revision, mutation, and consistency values for relation storage."""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zanzipy.models import RelationTuple


@dataclass(frozen=True, order=True, slots=True)
class Revision:
    """Monotonic datastore revision token."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise TypeError("revision value must be an int")
        if self.value < 0:
            raise ValueError("revision value must be non-negative")

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Result returned by tuple mutations."""

    revision: Revision


class RelationshipOperation(StrEnum):
    """Type of tuple change stored in a revision stream."""

    WRITE = "write"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class TupleMutation:
    """One relation tuple mutation inside a repository write batch."""

    relation_tuple: RelationTuple
    operation: RelationshipOperation

    @classmethod
    def touch(cls, relation_tuple: RelationTuple) -> TupleMutation:
        """Create or keep ``relation_tuple`` active."""

        return cls(relation_tuple=relation_tuple, operation=RelationshipOperation.WRITE)

    @classmethod
    def delete(cls, relation_tuple: RelationTuple) -> TupleMutation:
        """Mark ``relation_tuple`` inactive if it is active."""

        return cls(
            relation_tuple=relation_tuple,
            operation=RelationshipOperation.DELETE,
        )


@dataclass(frozen=True, slots=True)
class RelationshipChange:
    """One tuple change committed at a revision."""

    revision: Revision
    relation_tuple: RelationTuple
    operation: RelationshipOperation


class Consistency:
    """Marker base class for read consistency policies."""


@dataclass(frozen=True, slots=True)
class FullyConsistent(Consistency):
    """Read at the repository head revision."""


@dataclass(frozen=True, slots=True)
class AtLeastAsFresh(Consistency):
    """Read from a revision at least as fresh as ``revision``."""

    revision: Revision


@dataclass(frozen=True, slots=True)
class AtExactRevision(Consistency):
    """Read exactly at ``revision``."""

    revision: Revision


def revision_for_consistency(
    head_revision: Revision,
    consistency: Consistency | None,
) -> Revision:
    """Resolve a consistency policy to the revision used for a read."""

    if consistency is None or isinstance(consistency, FullyConsistent):
        return head_revision
    if isinstance(consistency, AtLeastAsFresh):
        if head_revision < consistency.revision:
            raise ValueError(
                "requested revision is newer than repository head: "
                f"requested {consistency.revision}, head {head_revision}"
            )
        return head_revision
    if isinstance(consistency, AtExactRevision):
        return consistency.revision
    raise TypeError(f"unknown consistency policy: {type(consistency).__name__}")
