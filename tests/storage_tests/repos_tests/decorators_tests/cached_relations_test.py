from zanzipy.models import RelationTuple, TupleFilter
from zanzipy.storage.cache.abstract.tuples import TupleCache
from zanzipy.storage.cache.concrete.lru import LruTupleCache
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository
from zanzipy.storage.repos.decorators.cached_relations import CachedRelationRepository
from zanzipy.storage.revision import (
    ReadContext,
    Revision,
    TenantId,
    TupleMutation,
    WriteContext,
)

TENANT = TenantId("default")
OTHER_TENANT = TenantId("other")


def _read_context(revision: Revision, tenant: TenantId = TENANT) -> ReadContext:
    return ReadContext(tenant, revision)


def _rt(
    object_ns: str,
    object_id: str,
    relation: str,
    subject_ns: str,
    subject_id: str,
    subject_rel: str | None = None,
) -> RelationTuple:
    suffix = "" if subject_rel is None else f"#{subject_rel}"
    return RelationTuple.from_string(
        f"{object_ns}:{object_id}#{relation}@{subject_ns}:{subject_id}{suffix}"
    )


class _FailingTupleCache(TupleCache):
    def __init__(
        self,
        *,
        fail_object_get: bool = False,
        fail_object_set: bool = False,
        fail_subject_get: bool = False,
        fail_subject_set: bool = False,
    ) -> None:
        self._fail_object_get = fail_object_get
        self._fail_object_set = fail_object_set
        self._fail_subject_get = fail_subject_get
        self._fail_subject_set = fail_subject_set

    def get_by_object(
        self,
        obj: object,
        *,
        context: ReadContext,
    ) -> tuple[RelationTuple, ...] | None:
        if self._fail_object_get:
            raise ConnectionError("object cache unavailable")
        return None

    def set_by_object(
        self,
        obj: object,
        *,
        context: ReadContext,
        tuples: object,
    ) -> None:
        if self._fail_object_set:
            raise PermissionError("object cache write denied")

    def get_by_subject(
        self,
        subject: object,
        *,
        context: ReadContext,
    ) -> tuple[RelationTuple, ...] | None:
        if self._fail_subject_get:
            raise ConnectionError("subject cache unavailable")
        return None

    def set_by_subject(
        self,
        subject: object,
        *,
        context: ReadContext,
        tuples: object,
    ) -> None:
        if self._fail_subject_set:
            raise PermissionError("subject cache write denied")


class TestCachedRelationRepository:
    def test_object_cache_get_failure_falls_back_to_backend(self) -> None:
        backend = InMemoryRelationRepository()
        cache = _FailingTupleCache(fail_object_get=True)
        repo = CachedRelationRepository(backend, cache=cache)
        tuple_ = _rt("doc", "1", "viewer", "user", "alice")
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(tuple_),))

        assert list(
            repo.by_object(tuple_.object, context=_read_context(write.revision))
        ) == [tuple_]

    def test_object_cache_set_failure_still_returns_backend_result(self) -> None:
        backend = InMemoryRelationRepository()
        cache = _FailingTupleCache(fail_object_set=True)
        repo = CachedRelationRepository(backend, cache=cache)
        tuple_ = _rt("doc", "1", "viewer", "user", "alice")
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(tuple_),))

        assert list(
            repo.by_object(tuple_.object, context=_read_context(write.revision))
        ) == [tuple_]

    def test_subject_cache_get_failure_falls_back_to_backend(self) -> None:
        backend = InMemoryRelationRepository()
        cache = _FailingTupleCache(fail_subject_get=True)
        repo = CachedRelationRepository(backend, cache=cache)
        tuple_ = _rt("doc", "1", "viewer", "user", "alice")
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(tuple_),))
        filt = TupleFilter(subject_type="user", subject_id="alice")

        assert list(repo.read_reverse(filt, context=_read_context(write.revision))) == [
            tuple_
        ]

    def test_subject_cache_set_failure_still_returns_backend_result(self) -> None:
        backend = InMemoryRelationRepository()
        cache = _FailingTupleCache(fail_subject_set=True)
        repo = CachedRelationRepository(backend, cache=cache)
        tuple_ = _rt("doc", "1", "viewer", "user", "alice")
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(tuple_),))
        filt = TupleFilter(subject_type="user", subject_id="alice")

        assert list(repo.read_reverse(filt, context=_read_context(write.revision))) == [
            tuple_
        ]

    def test_object_cache_is_revision_scoped(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        first = _rt("doc", "1", "viewer", "user", "alice")
        second = _rt("doc", "1", "viewer", "user", "bob")
        first_write = repo.write(WriteContext(TENANT), (TupleMutation.touch(first),))

        assert list(
            repo.by_object(first.object, context=_read_context(first_write.revision))
        ) == [first]

        second_write = repo.write(WriteContext(TENANT), (TupleMutation.touch(second),))

        assert list(
            repo.by_object(first.object, context=_read_context(first_write.revision))
        ) == [first]
        assert list(
            repo.by_object(first.object, context=_read_context(second_write.revision))
        ) == [first, second]

    def test_subject_cache_is_revision_scoped(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        first = _rt("doc", "1", "viewer", "user", "alice")
        second = _rt("doc", "2", "viewer", "user", "alice")
        first_write = repo.write(WriteContext(TENANT), (TupleMutation.touch(first),))
        second_write = repo.write(WriteContext(TENANT), (TupleMutation.touch(second),))
        filt = TupleFilter(subject_type="user", subject_id="alice")

        assert list(
            repo.read_reverse(filt, context=_read_context(first_write.revision))
        ) == [first]
        assert list(
            repo.read_reverse(filt, context=_read_context(second_write.revision))
        ) == [
            first,
            second,
        ]

    def test_constrained_reverse_filter_uses_subject_cache_bucket(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        matching = _rt("doc", "1", "viewer", "user", "alice")
        other_relation = _rt("doc", "2", "editor", "user", "alice")
        other_subject = _rt("doc", "3", "viewer", "user", "bob")
        write = repo.write(
            WriteContext(TENANT),
            (
                TupleMutation.touch(matching),
                TupleMutation.touch(other_relation),
                TupleMutation.touch(other_subject),
            ),
        )
        filt = TupleFilter(
            object_type="doc",
            relation="viewer",
            subject_type="user",
            subject_id="alice",
            subject_relation=TupleFilter.DIRECT_SUBJECT_RELATION,
        )

        assert list(repo.read_reverse(filt, context=_read_context(write.revision))) == [
            matching
        ]
        after_fill = cache.info()
        assert after_fill["misses"] == 1
        assert after_fill["size_subjects"] == 1

        assert list(repo.read_reverse(filt, context=_read_context(write.revision))) == [
            matching
        ]
        after_hit = cache.info()
        assert after_hit["hits"] == 1
        assert after_hit["size_subjects"] == 1

    def test_cache_does_not_return_deleted_tuple_at_newer_revision(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        tuple_ = _rt("doc", "1", "viewer", "user", "alice")
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(tuple_),))
        assert list(
            repo.by_object(tuple_.object, context=_read_context(write.revision))
        ) == [tuple_]

        delete = repo.write(WriteContext(TENANT), (TupleMutation.delete(tuple_),))

        assert list(
            repo.by_object(tuple_.object, context=_read_context(write.revision))
        ) == [tuple_]
        assert (
            list(repo.by_object(tuple_.object, context=_read_context(delete.revision)))
            == []
        )

    def test_subject_cache_old_revision_survives_newer_delete(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        tuple_ = _rt("doc", "1", "viewer", "user", "alice")
        filt = TupleFilter(subject_type="user", subject_id="alice")
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(tuple_),))
        assert list(repo.read_reverse(filt, context=_read_context(write.revision))) == [
            tuple_
        ]

        delete = repo.write(WriteContext(TENANT), (TupleMutation.delete(tuple_),))

        assert list(repo.read_reverse(filt, context=_read_context(write.revision))) == [
            tuple_
        ]
        assert (
            list(repo.read_reverse(filt, context=_read_context(delete.revision))) == []
        )

    def test_object_and_subject_caches_are_tenant_scoped(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        tenant_object = _rt("doc", "shared", "viewer", "user", "alice")
        tenant_subject = _rt("doc", "tenant-only", "viewer", "user", "carol")
        other_object = _rt("doc", "shared", "viewer", "user", "bob")
        other_subject = _rt("doc", "other-only", "viewer", "user", "carol")
        tenant_write = repo.write(
            WriteContext(TENANT),
            (TupleMutation.touch(tenant_object), TupleMutation.touch(tenant_subject)),
        )
        other_write = repo.write(
            WriteContext(OTHER_TENANT),
            (TupleMutation.touch(other_object), TupleMutation.touch(other_subject)),
        )

        assert tenant_write.revision == Revision(1)
        assert other_write.revision == Revision(1)
        assert list(
            repo.by_object(
                tenant_object.object,
                context=_read_context(tenant_write.revision, TENANT),
            )
        ) == [tenant_object]
        assert list(
            repo.by_object(
                other_object.object,
                context=_read_context(other_write.revision, OTHER_TENANT),
            )
        ) == [other_object]

        filt = TupleFilter(subject_type="user", subject_id="carol")
        assert list(
            repo.read_reverse(
                filt, context=_read_context(tenant_write.revision, TENANT)
            )
        ) == [tenant_subject]
        assert list(
            repo.read_reverse(
                filt,
                context=_read_context(other_write.revision, OTHER_TENANT),
            )
        ) == [other_subject]

    def test_mixed_filter_bypasses_cache_but_uses_revision(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        t1 = _rt("doc", "1", "viewer", "user", "alice")
        t2 = _rt("doc", "1", "viewer", "group", "eng", "member")
        write = repo.write(
            WriteContext(TENANT),
            (TupleMutation.touch(t1), TupleMutation.touch(t2)),
        )

        mixed = TupleFilter(object_type="doc", subject_type="user")
        assert list(repo.read(mixed, context=_read_context(write.revision))) == [t1]

    def test_direct_subject_filter_uses_broad_revision_bucket(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        direct = _rt("doc", "1", "viewer", "group", "eng")
        userset = _rt("doc", "2", "viewer", "group", "eng", "member")
        write = repo.write(
            WriteContext(TENANT),
            (TupleMutation.touch(direct), TupleMutation.touch(userset)),
        )

        exact = TupleFilter.from_subject(direct.subject)
        broad = TupleFilter(subject_type="group", subject_id="eng")
        assert list(
            repo.read_reverse(exact, context=_read_context(write.revision))
        ) == [direct]
        assert list(
            repo.read_reverse(broad, context=_read_context(write.revision))
        ) == [
            direct,
            userset,
        ]

    def test_watch_delegates_to_backend(self) -> None:
        backend = InMemoryRelationRepository()
        repo = CachedRelationRepository(
            backend,
            cache=LruTupleCache(max_entries=100, ttl_seconds=None),
        )
        tuple_ = _rt("doc", "1", "viewer", "user", "alice")
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(tuple_),))

        changes = list(repo.watch(TENANT, after=Revision(0)))

        assert [change.token for change in changes] == [write.token]
        assert [change.tenant for change in changes] == [TENANT]
        assert [change.relation_tuple for change in changes] == [tuple_]
