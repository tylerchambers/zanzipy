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

        # Populate backend and read through cache via by_object
        backend.upsert(t1)
        backend.upsert(t2)

        obj = t1.object
        res = list(repo.by_object(obj))
        assert sorted(map(str, res)) == sorted([str(t1), str(t2)])

        # Second call should hit cache; remove from backend to ensure cache used
        backend.delete_by_key(t1)
        backend.delete_by_key(t2)
        res2 = list(repo.by_object(obj))
        assert sorted(map(str, res2)) == sorted([str(t1), str(t2)])

        # Now invalidate by writing a new tuple to same object
        t3 = _rt("doc", "1", "owner", "user", "carol")
        repo.upsert(t3)

        # Backend currently lacks t1/t2 (deleted), but has t3 via upsert above
        # A by_object read should no longer return cached t1/t2
        fresh = list(repo.by_object(obj))
        assert list(map(str, fresh)) == [str(t3)]

    def test_reverse_read_through_and_invalidation_subject(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        t1 = _rt("doc", "1", "viewer", "user", "alice")
        t2 = _rt("doc", "2", "viewer", "user", "alice")
        backend.upsert(t1)
        backend.upsert(t2)

        # First reverse read populates subject cache
        filt = TupleFilter(subject_type="user", subject_id="alice")
        res = list(repo.read_reverse(filt))
        assert sorted(map(str, res)) == sorted([str(t1), str(t2)])

        # Remove from backend; cache should still serve previous results
        backend.delete_by_key(t1)
        backend.delete_by_key(t2)
        res2 = list(repo.read_reverse(filt))
        assert sorted(map(str, res2)) == sorted([str(t1), str(t2)])

        # Upsert a new tuple for same subject; should invalidate reverse cache
        t3 = _rt("doc", "3", "viewer", "user", "alice")
        repo.upsert(t3)
        res3 = list(repo.read_reverse(filt))
        assert list(map(str, res3)) == [str(t3)]

    def test_mixed_filter_bypasses_cache(self) -> None:
        backend = InMemoryRelationRepository()
        cache = LruTupleCache(max_entries=100, ttl_seconds=None)
        repo = CachedRelationRepository(backend, cache=cache)

        t1 = _rt("doc", "1", "viewer", "user", "alice")
        t2 = _rt("doc", "1", "viewer", "group", "eng", "member")
        backend.upsert(t1)
        backend.upsert(t2)

        # Mixed filter includes object_type and subject_type -> should bypass cache
        mixed = TupleFilter(object_type="doc", subject_type="user")
        res = list(repo.read(mixed))
        # In-memory backend will filter correctly and return t1 only
        assert list(map(str, res)) == [str(t1)]
