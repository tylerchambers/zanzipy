from zanzipy.models import RelationTuple, TupleFilter
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


class TestCachedRelationRepository:
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

        assert [change.revision for change in changes] == [write.revision]
        assert [change.relation_tuple for change in changes] == [tuple_]
