"""Deterministic in-memory tenant-scoped relation tuple repository."""

from collections.abc import Iterable, Iterator  # noqa: TC003

from zanzipy.models import RelationTuple, TupleFilter  # noqa: TC001
from zanzipy.storage.repos.abstract.relations import (
    RelationRepository,
    RelationWriteValidationMixin,
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


class InMemoryRelationRepository(RelationWriteValidationMixin, RelationRepository):
    """In-memory ``RelationRepository`` with exact tenant revision snapshots.

    The repository preserves insertion order for deterministic reads, stores a
    full snapshot per tenant and committed revision, and replays tenant-scoped
    changes through ``watch``. It is intentionally simple and not thread-safe.
    """

    def __init__(self) -> None:
        self._tuples: dict[str, dict[str, RelationTuple]] = {}
        self._snapshots: dict[str, dict[int, dict[str, RelationTuple]]] = {}
        self._revisions: dict[str, Revision] = {}
        self._changes: dict[str, list[RelationshipChange]] = {}

    def write(
        self,
        context: WriteContext,
        mutations: Iterable[TupleMutation],
    ) -> WriteResult:
        """Apply idempotent tuple writes and deletes at one tenant revision.

        Raises:
            ValueError: If a mutation contains an unknown operation.
        """
        mutations = self._validated_mutation_batch(mutations)
        tenant_key = str(context.tenant)
        self._ensure_tenant(tenant_key)
        staged = dict(self._tuples[tenant_key])
        changes: list[tuple[RelationTuple, RelationshipOperation]] = []
        for mutation in mutations:
            key = str(mutation.relation_tuple)
            if mutation.operation is RelationshipOperation.WRITE:
                if key in staged:
                    continue
                staged[key] = mutation.relation_tuple
                changes.append((mutation.relation_tuple, mutation.operation))
                continue
            if mutation.operation is RelationshipOperation.DELETE:
                deleted = staged.pop(key, None)
                if deleted is not None:
                    changes.append((deleted, mutation.operation))
                continue
            raise ValueError(f"unknown tuple mutation operation: {mutation.operation}")
        if changes:
            self._tuples[tenant_key] = staged
        return self._commit(context.tenant, changes)

    def head_revision(self, tenant: TenantId) -> Revision:
        """Return the latest in-memory revision for ``tenant``."""
        return self._revisions.get(str(tenant), Revision(0))

    def get(
        self,
        key: RelationTuple,
        *,
        context: ReadContext,
    ) -> RelationTuple | None:
        """Return ``key`` when it is visible in the requested tenant snapshot.

        Raises:
            ValueError: If ``context.revision`` is newer than the tenant head.
        """
        tuple_key = str(key)
        snapshot = self._snapshot_at(context)
        if tuple_key not in snapshot:
            return None
        return snapshot[tuple_key]

    def read(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Return tuples matching ``filter`` in tenant snapshot insertion order.

        Raises:
            ValueError: If ``context.revision`` is newer than the tenant head.
        """
        snapshot = self._snapshot_at(context)
        return [tuple_ for tuple_ in snapshot.values() if filter.matches(tuple_)]

    def read_reverse(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Return matching tuples using the same in-memory scan as ``read``.

        Raises:
            ValueError: If ``context.revision`` is newer than the tenant head.
        """
        return self.read(filter, context=context)

    def watch(
        self,
        tenant: TenantId,
        *,
        after: Revision,
    ) -> Iterator[RelationshipChange]:
        """Yield committed tuple changes for ``tenant`` after ``after``.

        Raises:
            ValueError: If ``after`` is newer than the tenant head revision.
        """
        head = self.head_revision(tenant)
        if after > head:
            raise ValueError(f"requested revision {after} is newer than head {head}")
        for change in self._changes.get(str(tenant), []):
            if change.token.revision > after:
                yield change

    def info(self) -> dict[str, object]:
        """Return backend diagnostics for tenant revisions and active tuple count."""
        return {
            "backend": "memory",
            "head_revisions": {
                tenant: revision.value for tenant, revision in self._revisions.items()
            },
            "tuples": sum(len(tuples) for tuples in self._tuples.values()),
        }

    def _ensure_tenant(self, tenant_key: str) -> None:
        if tenant_key in self._revisions:
            return
        self._tuples[tenant_key] = {}
        self._snapshots[tenant_key] = {0: {}}
        self._revisions[tenant_key] = Revision(0)
        self._changes[tenant_key] = []

    def _touch(self, tenant_key: str, relation_tuple: RelationTuple) -> bool:
        key = str(relation_tuple)
        tuples = self._tuples[tenant_key]
        if key in tuples:
            return False
        tuples[key] = relation_tuple
        return True

    def _delete(
        self,
        tenant_key: str,
        relation_tuple: RelationTuple,
    ) -> RelationTuple | None:
        return self._tuples[tenant_key].pop(str(relation_tuple), None)

    def _commit(
        self,
        tenant: TenantId,
        changes: Iterable[tuple[RelationTuple, RelationshipOperation]],
    ) -> WriteResult:
        changes = tuple(changes)
        tenant_key = str(tenant)
        revision = self._revisions[tenant_key]
        if not changes:
            return WriteResult(RevisionToken(tenant, revision))
        revision = Revision(revision.value + 1)
        self._revisions[tenant_key] = revision
        self._snapshots[tenant_key][revision.value] = dict(self._tuples[tenant_key])
        self._changes[tenant_key].extend(
            RelationshipChange(
                token=RevisionToken(tenant, revision),
                relation_tuple=relation_tuple,
                operation=operation,
            )
            for relation_tuple, operation in changes
        )
        return WriteResult(RevisionToken(tenant, revision))

    def _snapshot_at(self, context: ReadContext) -> dict[str, RelationTuple]:
        tenant_key = str(context.tenant)
        head = self.head_revision(context.tenant)
        if context.revision > head:
            raise ValueError(
                f"requested revision {context.revision} is newer than head {head}"
            )
        if tenant_key not in self._snapshots:
            return {}
        try:
            return self._snapshots[tenant_key][context.revision.value]
        except KeyError as exc:
            raise ValueError(
                f"unknown relation repository revision {context.revision}"
            ) from exc


def touch(relation_tuple: RelationTuple) -> TupleMutation:
    """Return a touch mutation for concise tests and examples."""

    return TupleMutation.touch(relation_tuple)


def delete(relation_tuple: RelationTuple) -> TupleMutation:
    """Return a delete mutation for concise tests and examples."""

    return TupleMutation.delete(relation_tuple)
