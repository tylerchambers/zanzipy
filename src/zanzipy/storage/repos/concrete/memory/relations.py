"""Deterministic in-memory revisioned relation tuple repository."""

from collections.abc import Iterable, Iterator  # noqa: TC003

from zanzipy.models import RelationTuple, TupleFilter  # noqa: TC001
from zanzipy.storage.repos.abstract.relations import RelationRepository
from zanzipy.storage.revision import (
    RelationshipChange,
    RelationshipOperation,
    Revision,
    TupleMutation,
    WriteResult,
)


class InMemoryRelationRepository(RelationRepository):
    """In-memory ``RelationRepository`` with exact revision snapshots.

    The repository preserves insertion order for deterministic reads, stores a
    full snapshot per committed revision, and replays committed changes through
    ``watch``. It is intentionally simple and not thread-safe.
    """

    def __init__(self) -> None:
        self._tuples: dict[str, RelationTuple] = {}
        self._snapshots: dict[int, dict[str, RelationTuple]] = {0: {}}
        self._revision = Revision(0)
        self._changes: list[RelationshipChange] = []

    def write(self, mutations: Iterable[TupleMutation]) -> WriteResult:
        """Apply idempotent tuple writes and deletes at one new revision.

        Raises:
            ValueError: If a mutation contains an unknown operation.
        """
        changes: list[tuple[RelationTuple, RelationshipOperation]] = []
        for mutation in mutations:
            if mutation.operation is RelationshipOperation.WRITE:
                if self._touch(mutation.relation_tuple):
                    changes.append((mutation.relation_tuple, mutation.operation))
                continue
            if mutation.operation is RelationshipOperation.DELETE:
                deleted = self._delete(mutation.relation_tuple)
                if deleted is not None:
                    changes.append((deleted, mutation.operation))
                continue
            raise ValueError(f"unknown tuple mutation operation: {mutation.operation}")
        return self._commit(changes)

    def head_revision(self) -> Revision:
        """Return the latest in-memory revision."""
        return self._revision

    def get(
        self,
        key: RelationTuple,
        *,
        revision: Revision,
    ) -> RelationTuple | None:
        """Return ``key`` when it is visible in the requested snapshot.

        Raises:
            ValueError: If ``revision`` is not a known snapshot.
        """
        tuple_key = str(key)
        snapshot = self._snapshot_at(revision)
        if tuple_key not in snapshot:
            return None
        return snapshot[tuple_key]

    def read(
        self,
        filter: TupleFilter,
        *,
        revision: Revision,
    ) -> Iterable[RelationTuple]:
        """Return tuples matching ``filter`` in snapshot insertion order.

        Raises:
            ValueError: If ``revision`` is not a known snapshot.
        """
        snapshot = self._snapshot_at(revision)
        return [tuple_ for tuple_ in snapshot.values() if filter.matches(tuple_)]

    def read_reverse(
        self,
        filter: TupleFilter,
        *,
        revision: Revision,
    ) -> Iterable[RelationTuple]:
        """Return matching tuples using the same in-memory scan as ``read``.

        Raises:
            ValueError: If ``revision`` is not a known snapshot.
        """
        return self.read(filter, revision=revision)

    def watch(self, *, after: Revision) -> Iterator[RelationshipChange]:
        """Yield committed tuple changes after ``after`` in commit order.

        Raises:
            ValueError: If ``after`` is newer than the head revision.
        """
        if after > self._revision:
            raise ValueError(
                f"requested revision {after} is newer than head {self._revision}"
            )
        for change in self._changes:
            if change.revision > after:
                yield change

    def info(self) -> dict[str, object]:
        """Return backend diagnostics for revision and active tuple count."""
        return {
            "backend": "memory",
            "head_revision": self._revision.value,
            "tuples": len(self._tuples),
        }

    def _touch(self, relation_tuple: RelationTuple) -> bool:
        key = str(relation_tuple)
        if key in self._tuples:
            return False
        self._tuples[key] = relation_tuple
        return True

    def _delete(self, relation_tuple: RelationTuple) -> RelationTuple | None:
        return self._tuples.pop(str(relation_tuple), None)

    def _commit(
        self,
        changes: Iterable[tuple[RelationTuple, RelationshipOperation]],
    ) -> WriteResult:
        changes = tuple(changes)
        if not changes:
            return WriteResult(self._revision)
        self._revision = Revision(self._revision.value + 1)
        self._snapshots[self._revision.value] = dict(self._tuples)
        self._changes.extend(
            RelationshipChange(
                revision=self._revision,
                relation_tuple=relation_tuple,
                operation=operation,
            )
            for relation_tuple, operation in changes
        )
        return WriteResult(self._revision)

    def _snapshot_at(self, revision: Revision) -> dict[str, RelationTuple]:
        try:
            return self._snapshots[revision.value]
        except KeyError as exc:
            if revision > self._revision:
                raise ValueError(
                    f"requested revision {revision} is newer than head {self._revision}"
                ) from exc
            raise ValueError(
                f"unknown relation repository revision {revision}"
            ) from exc


def touch(relation_tuple: RelationTuple) -> TupleMutation:
    """Return a touch mutation for concise tests and examples."""

    return TupleMutation.touch(relation_tuple)


def delete(relation_tuple: RelationTuple) -> TupleMutation:
    """Return a delete mutation for concise tests and examples."""

    return TupleMutation.delete(relation_tuple)
