from zanzipy.models.filter import TupleFilter
from zanzipy.models.tuple import RelationTuple
from zanzipy.storage.cache.concrete.lru import LruTupleCache
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository
from zanzipy.storage.repos.decorators.cached_relations import CachedRelationRepository


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
    def test_read_through_and_invalidation_object(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        t1 = _rt("doc", "1", "viewer", "user", "alice")
        t2 = _rt("doc", "1", "editor", "user", "bob")

        backend.upsert(t1)
        backend.upsert(t2)

        obj = t1.object
        assert list(repo.by_object(obj)) == [t1, t2]

        backend.delete_by_key(t1)
        backend.delete_by_key(t2)
        assert list(repo.by_object(obj)) == [t1, t2]

        t3 = _rt("doc", "1", "owner", "user", "carol")
        repo.upsert(t3)

        assert list(repo.by_object(obj)) == [t3]

    def test_reverse_read_through_and_invalidation_subject(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        t1 = _rt("doc", "1", "viewer", "user", "alice")
        t2 = _rt("doc", "2", "viewer", "user", "alice")
        backend.upsert(t1)
        backend.upsert(t2)

        filt = TupleFilter(subject_type="user", subject_id="alice")
        assert list(repo.read_reverse(filt)) == [t1, t2]

        backend.delete_by_key(t1)
        backend.delete_by_key(t2)
        assert list(repo.read_reverse(filt)) == [t1, t2]

        t3 = _rt("doc", "3", "viewer", "user", "alice")
        repo.upsert(t3)
        assert list(repo.read_reverse(filt)) == [t3]

    def test_mixed_filter_bypasses_cache(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        t1 = _rt("doc", "1", "viewer", "user", "alice")
        t2 = _rt("doc", "1", "viewer", "group", "eng", "member")
        backend.upsert(t1)
        backend.upsert(t2)

        mixed = TupleFilter(object_type="doc", subject_type="user")
        assert list(repo.read(mixed)) == [t1]

    def test_relation_filtered_reverse_read_caches_broad_subject_bucket(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        viewer = _rt("doc", "1", "viewer", "user", "alice")
        owner = _rt("doc", "2", "owner", "user", "alice")
        backend.write_many([viewer, owner])

        filtered = TupleFilter(
            subject_type="user",
            subject_id="alice",
            relation="viewer",
        )
        assert list(repo.read_reverse(filtered)) == [viewer]

        backend.delete_many([viewer, owner])
        broad = TupleFilter(subject_type="user", subject_id="alice")
        assert list(repo.read_reverse(broad)) == [viewer, owner]

    def test_subject_set_write_invalidates_broad_subject_bucket(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        first = _rt("doc", "1", "viewer", "group", "eng", "member")
        backend.write(first)
        broad = TupleFilter(subject_type="group", subject_id="eng")
        assert list(repo.read_reverse(broad)) == [first]

        backend.delete(first)
        second = _rt("doc", "2", "viewer", "group", "eng", "member")
        repo.write(second)

        assert list(repo.read_reverse(broad)) == [second]

    def test_subject_set_delete_invalidates_broad_subject_bucket(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        stale = _rt("doc", "1", "viewer", "group", "eng", "member")
        backend.write(stale)
        broad = TupleFilter(subject_type="group", subject_id="eng")
        assert list(repo.read_reverse(broad)) == [stale]

        assert repo.delete(stale) is True
        assert list(repo.read_reverse(broad)) == []

    def test_delete_many_by_key_materializes_generator(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        tuples = [
            _rt("doc", "1", "viewer", "user", "alice"),
            _rt("doc", "2", "viewer", "user", "alice"),
        ]
        repo.write_many(tuples)
        filt = TupleFilter(subject_type="user", subject_id="alice")
        assert list(repo.read_reverse(filt)) == tuples

        deleted = repo.delete_many_by_key(tuple_ for tuple_ in tuples)

        assert deleted == 2
        assert list(repo.read_reverse(filt)) == []
