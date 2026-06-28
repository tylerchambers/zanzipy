"""Tenant-scoped, revisioned storage contract for Zanzibar relation tuples."""

from typing import TYPE_CHECKING, Any, Protocol, Self, runtime_checkable

from zanzipy.models import TupleFilter
from zanzipy.storage.revision import RelationshipOperation

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from zanzipy.models import Obj, Relation, RelationTuple, Subject
    from zanzipy.storage.revision import (
        ReadContext,
        RelationshipChange,
        Revision,
        TenantId,
        TupleMutation,
        WriteContext,
        WriteResult,
    )


class RelationWriteValidationMixin:
    """Shared write-batch validation for relation repository implementations."""

    def _validated_mutation_batch(
        self,
        mutations: Iterable[TupleMutation],
    ) -> tuple[TupleMutation, ...]:
        """Return a write batch after rejecting ambiguous same-tuple operations.

        Durable repositories store writes and deletes by revision, not by a
        per-mutation sequence. A batch that both writes and deletes the same
        tuple cannot be replayed consistently from ``watch``, so reject it
        before any backend state changes.
        """

        batch = tuple(mutations)
        operations_by_key: dict[str, RelationshipOperation] = {}
        for mutation in batch:
            operation = mutation.operation
            if (
                operation is not RelationshipOperation.WRITE
                and operation is not RelationshipOperation.DELETE
            ):
                continue

            tuple_key = str(mutation.relation_tuple)
            previous = operations_by_key.setdefault(tuple_key, operation)
            if previous is not operation:
                raise ValueError(
                    f"conflicting tuple mutations for {tuple_key} in one write batch"
                )
        return batch


@runtime_checkable
class RelationRepository(Protocol):
    """Repository contract for tenant-scoped durable relation tuple storage."""

    def write(
        self,
        context: WriteContext,
        mutations: Iterable[TupleMutation],
    ) -> WriteResult:
        """Apply ``mutations`` atomically at one tenant revision."""

    def head_revision(self, tenant: TenantId) -> Revision:
        """Return the latest datastore revision available for ``tenant``."""

    def get(
        self,
        key: RelationTuple,
        *,
        context: ReadContext,
    ) -> RelationTuple | None:
        """Return ``key`` in ``context`` if present."""

    def read(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Return tuples matching ``filter`` in ``context`` using a forward path."""

    def read_reverse(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Return tuples matching ``filter`` in ``context`` using a reverse path."""

    def by_object(
        self,
        obj: Obj,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Return tuples for ``obj`` in ``context``."""

        return self.read(TupleFilter.from_object(obj), context=context)

    def by_subject(
        self,
        subject: Subject,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Return tuples for ``subject`` in ``context`` using the reverse path."""

        return self.read_reverse(TupleFilter.from_subject(subject), context=context)

    def by_relation(
        self,
        relation: Relation,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Return tuples with relation name ``relation`` in ``context``."""

        return self.read(TupleFilter.from_relation(relation), context=context)

    def watch(
        self,
        tenant: TenantId,
        *,
        after: Revision,
    ) -> Iterator[RelationshipChange]:
        """Yield ``tenant`` tuple changes committed after ``after``."""

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
