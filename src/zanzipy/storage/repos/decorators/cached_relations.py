"""Tenant/revision-aware read-through cache decorator for relation repositories."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from zanzipy.storage.repos.abstract.relations import RelationRepository

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from zanzipy.models import Obj, RelationTuple, Subject, TupleFilter
    from zanzipy.storage.cache.abstract.tuples import TupleCache
    from zanzipy.storage.revision import (
        ReadContext,
        RelationshipChange,
        Revision,
        TenantId,
        TupleMutation,
        WriteContext,
        WriteResult,
    )


@dataclass(slots=True)
class CachedRelationRepository(RelationRepository):
    """Add tenant/revision-scoped read-through caching to a relation repository.

    Writes, single-key reads, watches, and diagnostics delegate to the backend;
    cached bucket entries are keyed by tenant and revision, so immutable
    snapshots do not need invalidation after later writes.
    """

    backend: RelationRepository
    cache: TupleCache

    def write(
        self,
        context: WriteContext,
        mutations: Iterable[TupleMutation],
    ) -> WriteResult:
        """Delegate writes without clearing tenant revision-scoped cache entries.

        Existing cache entries remain valid because they describe immutable
        tenant revisions; reads for a newly written revision fill separate entries.
        """
        return self.backend.write(context, mutations)

    def head_revision(self, tenant: TenantId) -> Revision:
        """Return the backend head revision for ``tenant``."""
        return self.backend.head_revision(tenant)

    def get(
        self,
        key: RelationTuple,
        *,
        context: ReadContext,
    ) -> RelationTuple | None:
        """Delegate exact tuple lookup to the backend."""
        return self.backend.get(key, context=context)

    def read(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Read through cached object or subject buckets when filters allow it.

        Non-bucket filters delegate to the backend without caching.
        """
        if filter.is_object_bucket:
            obj = filter.object_ref
            assert obj is not None
            return [
                tuple_
                for tuple_ in self._object_bucket(obj, context=context)
                if filter.matches(tuple_)
            ]

        if filter.is_subject_bucket:
            subject = filter.subject_ref
            assert subject is not None
            tuples = self._subject_bucket(
                subject,
                filter.subject_bucket_filter(),
                context=context,
            )
            return [tuple_ for tuple_ in tuples if filter.matches(tuple_)]

        return self.backend.read(filter, context=context)

    def read_reverse(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        """Use cached subject buckets for reverse reads, otherwise delegate."""
        if filter.is_subject_bucket:
            return self.read(filter, context=context)
        return self.backend.read_reverse(filter, context=context)

    def watch(
        self,
        tenant: TenantId,
        *,
        after: Revision,
    ) -> Iterator[RelationshipChange]:
        """Delegate watch streams directly to the backend."""
        return self.backend.watch(tenant, after=after)

    def ping(self) -> bool:
        """Return whether both backend and cache are reachable."""
        return self.backend.ping() and self.cache.ping()

    def info(self) -> dict[str, object]:
        """Return decorator diagnostics with nested backend and cache info."""
        return {
            "decorator": "CachedRelationRepository",
            "backend": self.backend.info(),
            "cache": self.cache.info(),
        }

    def close(self) -> None:
        """Close the backend and cache, always attempting both."""
        try:
            self.backend.close()
        finally:
            self.cache.close()

    def _object_bucket(
        self,
        obj: Obj,
        *,
        context: ReadContext,
    ) -> list[RelationTuple]:
        cached = self.cache.get_by_object(obj, context=context)
        if cached is not None:
            return list(cached)
        result = list(self.backend.by_object(obj, context=context))
        self.cache.set_by_object(obj, context=context, tuples=result)
        return result

    def _subject_bucket(
        self,
        subject: Subject,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> list[RelationTuple]:
        cached = self.cache.get_by_subject(subject, context=context)
        if cached is not None:
            return list(cached)
        result = list(self.backend.read_reverse(filter, context=context))
        self.cache.set_by_subject(subject, context=context, tuples=result)
        return result
