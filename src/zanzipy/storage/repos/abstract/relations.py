"""Revisioned storage contract for Zanzibar relation tuples."""

from typing import TYPE_CHECKING, Any, Protocol, Self, runtime_checkable

from zanzipy.models import TupleFilter

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from zanzipy.models import Obj, Relation, RelationTuple, Subject
    from zanzipy.storage.revision import (
        RelationshipChange,
        Revision,
        TupleMutation,
        WriteResult,
    )


@runtime_checkable
class RelationRepository(Protocol):
    """Repository contract for revision-scoped durable relation tuple storage."""

    def write(self, mutations: Iterable[TupleMutation]) -> WriteResult:
        """Apply ``mutations`` atomically at one revision."""

    def head_revision(self) -> Revision:
        """Return the latest datastore revision available for reads."""

    def get(
        self,
        key: RelationTuple,
        *,
        revision: Revision,
    ) -> RelationTuple | None:
        """Return ``key`` at ``revision`` if present."""

    def read(
        self,
        filter: TupleFilter,
        *,
        revision: Revision,
    ) -> Iterable[RelationTuple]:
        """Return tuples matching ``filter`` at ``revision`` using a forward path."""

    def read_reverse(
        self,
        filter: TupleFilter,
        *,
        revision: Revision,
    ) -> Iterable[RelationTuple]:
        """Return tuples matching ``filter`` at ``revision`` using a reverse path."""

    def by_object(
        self,
        obj: Obj,
        *,
        revision: Revision,
    ) -> Iterable[RelationTuple]:
        """Return tuples for ``obj`` at ``revision``."""

        return self.read(TupleFilter.from_object(obj), revision=revision)

    def by_subject(
        self,
        subject: Subject,
        *,
        revision: Revision,
    ) -> Iterable[RelationTuple]:
        """Return tuples for ``subject`` at ``revision`` using the reverse path."""

        return self.read_reverse(TupleFilter.from_subject(subject), revision=revision)

    def by_relation(
        self,
        relation: Relation,
        *,
        revision: Revision,
    ) -> Iterable[RelationTuple]:
        """Return tuples with relation name ``relation`` at ``revision``."""

        return self.read(TupleFilter.from_relation(relation), revision=revision)

    def watch(self, *, after: Revision) -> Iterator[RelationshipChange]:
        """Yield tuple changes committed after ``after``."""

    def ping(self) -> bool:
        """Return whether the repository is reachable."""

        return True

    def info(self) -> dict[str, Any]:
        """Return backend diagnostics."""

        return {}

    def close(self) -> None:
        """Release backend resources."""

        return None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
        return None
