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


class _InspectableRelationRepository(InMemoryRelationRepository):
    def __init__(self) -> None:
        super().__init__()
        self.head_revision_calls = 0
        self.get_calls = 0
        self.read_calls = 0
        self.read_reverse_calls = 0
        self.ping_calls = 0
        self.close_calls = 0
        self.ping_result = True

    def head_revision(self, tenant: TenantId) -> Revision:
        self.head_revision_calls += 1
        return super().head_revision(tenant)

    def get(
        self,
        key: RelationTuple,
        *,
        context: ReadContext,
    ) -> RelationTuple | None:
        self.get_calls += 1
        return super().get(key, context=context)

    def read(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> list[RelationTuple]:
        self.read_calls += 1
        return list(super().read(filter, context=context))

    def read_reverse(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> list[RelationTuple]:
        self.read_reverse_calls += 1
        return list(super().read_reverse(filter, context=context))

    def ping(self) -> bool:
        self.ping_calls += 1
        return self.ping_result

    def info(self) -> dict[str, object]:
        return {
            "backend": "inspectable",
            "reads": self.read_calls,
            "reverse_reads": self.read_reverse_calls,
        }

    def close(self) -> None:
        self.close_calls += 1


class _InspectableTupleCache(TupleCache):
    def __init__(
        self,
        *,
        object_bucket: tuple[RelationTuple, ...] | None = None,
        subject_bucket: tuple[RelationTuple, ...] | None = None,
        ping_result: bool = True,
    ) -> None:
        self.object_bucket = object_bucket
        self.subject_bucket = subject_bucket
        self.ping_result = ping_result
        self.object_gets = 0
        self.object_sets: list[tuple[RelationTuple, ...]] = []
        self.subject_gets = 0
        self.subject_sets: list[tuple[RelationTuple, ...]] = []
        self.ping_calls = 0
        self.close_calls = 0

    def get_by_object(
        self,
        obj: object,
        *,
        context: ReadContext,
    ) -> tuple[RelationTuple, ...] | None:
        self.object_gets += 1
        return self.object_bucket

    def set_by_object(
        self,
        obj: object,
        *,
        context: ReadContext,
        tuples: object,
    ) -> None:
        bucket = tuple(tuples)  # type: ignore[arg-type]
        self.object_sets.append(bucket)
        self.object_bucket = bucket

    def get_by_subject(
        self,
        subject: object,
        *,
        context: ReadContext,
    ) -> tuple[RelationTuple, ...] | None:
        self.subject_gets += 1
        return self.subject_bucket

    def set_by_subject(
        self,
        subject: object,
        *,
        context: ReadContext,
        tuples: object,
    ) -> None:
        bucket = tuple(tuples)  # type: ignore[arg-type]
        self.subject_sets.append(bucket)
        self.subject_bucket = bucket

    def ping(self) -> bool:
        self.ping_calls += 1
        return self.ping_result

    def info(self) -> dict[str, object]:
        return {
            "cache": "inspectable",
            "object_gets": self.object_gets,
            "subject_gets": self.subject_gets,
        }

    def close(self) -> None:
        self.close_calls += 1


class TestCachedRelationRepository:
    def test_object_cache_hit_returns_cached_bucket_without_backend_read(self) -> None:
        backend = _InspectableRelationRepository()
        cached = _rt("doc", "1", "viewer", "user", "alice")
        cache = _InspectableTupleCache(object_bucket=(cached,))
        repo = CachedRelationRepository(backend, cache=cache)

        result = list(repo.by_object(cached.object, context=_read_context(Revision(9))))

        assert result == [cached]
        assert backend.read_calls == 0
        assert cache.object_gets == 1
        assert cache.object_sets == []

    def test_subject_cache_hit_filters_bucket_without_backend_reverse_read(
        self,
    ) -> None:
        backend = _InspectableRelationRepository()
        direct = _rt("doc", "1", "viewer", "group", "eng")
        userset = _rt("doc", "2", "viewer", "group", "eng", "member")
        cache = _InspectableTupleCache(subject_bucket=(direct, userset))
        repo = CachedRelationRepository(backend, cache=cache)
        filter_ = TupleFilter.from_subject(direct.subject)

        result = list(repo.read_reverse(filter_, context=_read_context(Revision(9))))

        assert result == [direct]
        assert backend.read_reverse_calls == 0
        assert cache.subject_gets == 1
        assert cache.subject_sets == []

    def test_non_subject_reverse_filter_delegates_without_cache_lookup(self) -> None:
        backend = _InspectableRelationRepository()
        cache = _InspectableTupleCache()
        repo = CachedRelationRepository(backend, cache=cache)
        tuple_ = _rt("doc", "1", "viewer", "user", "alice")
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(tuple_),))

        result = list(
            repo.read_reverse(
                TupleFilter(relation="viewer"),
                context=_read_context(write.revision),
            )
        )

        assert result == [tuple_]
        assert backend.read_reverse_calls == 1
        assert cache.object_gets == 0
        assert cache.subject_gets == 0

    def test_delegated_helpers_use_backend_and_cache_diagnostics(self) -> None:
        backend = _InspectableRelationRepository()
        cache = _InspectableTupleCache()
        repo = CachedRelationRepository(backend, cache=cache)
        tuple_ = _rt("doc", "1", "viewer", "user", "alice")
        write = repo.write(WriteContext(TENANT), (TupleMutation.touch(tuple_),))

        assert repo.head_revision(TENANT) == write.revision
        assert backend.head_revision_calls == 1
        assert repo.get(tuple_, context=_read_context(write.revision)) == tuple_
        assert backend.get_calls == 1
        assert repo.ping() is True
        assert backend.ping_calls == 1
        assert cache.ping_calls == 1
        assert repo.info() == {
            "decorator": "CachedRelationRepository",
            "backend": {
                "backend": "inspectable",
                "reads": 0,
                "reverse_reads": 0,
            },
            "cache": {
                "cache": "inspectable",
                "object_gets": 0,
                "subject_gets": 0,
            },
        }

        repo.close()

        assert backend.close_calls == 1
        assert cache.close_calls == 1

    def test_ping_short_circuits_cache_when_backend_is_unhealthy(self) -> None:
        backend = _InspectableRelationRepository()
        backend.ping_result = False
        cache = _InspectableTupleCache()
        repo = CachedRelationRepository(backend, cache=cache)

        assert repo.ping() is False
        assert backend.ping_calls == 1
        assert cache.ping_calls == 0

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
