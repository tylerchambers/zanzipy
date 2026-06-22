from zanzipy.models.tuple import RelationTuple
from zanzipy.storage.cache.concrete.lru import LruTupleCache


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


class TestLruTupleCache:
    def test_get_set_invalidate_by_object(self) -> None:
        cache = LruTupleCache(max_entries=10, ttl_seconds=None)
        t1 = _rt("doc", "1", "viewer", "user", "alice")
        obj = t1.object

        # Miss before set
        assert cache.get_by_object(obj) is None
        info = cache.info()
        assert info["misses"] == 1

        # Set and hit
        cache.set_by_object(obj, [t1])
        got = cache.get_by_object(obj)
        assert got is not None
        assert list(got) == [t1]

        # Returned list is a copy
        got_mut = list(got)
        got_mut.append(_rt("doc", "1", "viewer", "user", "bob"))
        got2 = cache.get_by_object(obj)
        assert got2 is not None
        assert list(got2) == [t1]

        # Invalidate
        cache.invalidate_object(obj)
        assert cache.get_by_object(obj) is None

    def test_get_set_invalidate_by_subject_variants(self) -> None:
        cache = LruTupleCache(max_entries=10, ttl_seconds=None)
        t_direct = _rt("doc", "1", "editor", "user", "alice")
        t_anchor = _rt("doc", "1", "editor", "group", "eng", "member")

        # Direct subject
        cache.set_by_subject(t_direct.subject, [t_direct])
        s1 = cache.get_by_subject(t_direct.subject)
        assert s1 is not None
        assert list(s1) == [t_direct]

        # Subject-set anchor is a distinct key
        cache.set_by_subject(t_anchor.subject, [t_anchor])
        s2 = cache.get_by_subject(t_anchor.subject)
        assert s2 is not None
        assert list(s2) == [t_anchor]

        # Invalidate only the direct subject
        cache.invalidate_subject(t_direct.subject)
        assert cache.get_by_subject(t_direct.subject) is None
        s3 = cache.get_by_subject(t_anchor.subject)
        assert s3 is not None
        assert list(s3) == [t_anchor]

    def test_ttl_expiry_zero_expires_immediately(self) -> None:
        cache = LruTupleCache(max_entries=10, ttl_seconds=0)
        t1 = _rt("doc", "1", "viewer", "user", "alice")
        cache.set_by_object(t1.object, [t1])
        # Immediate get should see expired entry
        res = cache.get_by_object(t1.object)
        assert res is None

    def test_ttl_expiry_with_time_advance(self, monkeypatch) -> None:
        # Use TTL of 5 and advance time beyond expiry
        cache = LruTupleCache(max_entries=10, ttl_seconds=5)
        # Patch the module's time.monotonic used by cache
        import zanzipy.storage.cache.concrete.lru as lru_module

        now = 1000.0

        def fake_monotonic() -> float:
            return now

        monkeypatch.setattr(lru_module.time, "monotonic", fake_monotonic)

        t1 = _rt("doc", "1", "viewer", "user", "alice")
        cache.set_by_object(t1.object, [t1])
        got = cache.get_by_object(t1.object)
        assert got is not None
        assert list(got) == [t1]

        # Advance time past expiry
        now = 1006.0
        assert cache.get_by_object(t1.object) is None

    def test_lru_eviction_prefers_larger_map_and_lru_order(self) -> None:
        # max_entries=3 -> evict one when inserting 4th
        cache = LruTupleCache(max_entries=3, ttl_seconds=None)
        t_a = _rt("doc", "A", "viewer", "user", "a")
        t_b = _rt("doc", "B", "viewer", "user", "b")
        t_c = _rt("doc", "C", "viewer", "user", "c")
        t_subj = _rt("doc", "S", "viewer", "user", "s")

        # Fill: two object keys, one subject key
        cache.set_by_object(t_a.object, [t_a])  # LRU oldest in obj_map
        cache.set_by_object(t_b.object, [t_b])  # newer in obj_map
        cache.set_by_subject(t_subj.subject, [t_subj])  # subj_map has 1

        # Touch oldest (t_a) to make t_b the LRU in obj_map
        g1 = cache.get_by_object(t_a.object)
        assert g1 is not None
        assert list(g1) == [t_a]

        # Insert a new object entry; obj_map is larger, so evict LRU from obj_map
        cache.set_by_object(t_c.object, [t_c])

        # t_b should be evicted; t_a (touched) and t_c should remain
        assert cache.get_by_object(t_b.object) is None
        g2 = cache.get_by_object(t_a.object)
        assert g2 is not None
        assert list(g2) == [t_a]
        g3 = cache.get_by_object(t_c.object)
        assert g3 is not None
        assert list(g3) == [t_c]
        # Subject entry remains
        s = cache.get_by_subject(t_subj.subject)
        assert s is not None
        assert list(s) == [t_subj]

        info = cache.info()
        assert info["size_objects"] == 2
        assert info["size_subjects"] == 1

    def test_counters_and_lifecycle(self) -> None:
        cache = LruTupleCache(max_entries=5, ttl_seconds=None)
        t1 = _rt("doc", "1", "viewer", "user", "alice")

        # Miss then hit
        assert cache.get_by_object(t1.object) is None
        cache.set_by_object(t1.object, [t1])
        got = cache.get_by_object(t1.object)
        assert got is not None
        assert list(got) == [t1]

        info = cache.info()
        hits = info["hits"]
        misses = info["misses"]
        assert isinstance(hits, int)
        assert hits >= 1
        assert isinstance(misses, int)
        assert misses >= 1

        # ping/close
        ok = cache.ping()
        assert ok is True
        cache.close()
        info2 = cache.info()
        assert info2["size_objects"] == 0
        assert info2["size_subjects"] == 0
