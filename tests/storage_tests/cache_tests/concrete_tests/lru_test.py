import pytest

from zanzipy.models.tuple import RelationTuple
from zanzipy.storage.cache.concrete.lru import LruTupleCache
from zanzipy.storage.revision import ReadContext, Revision, TenantId

DEFAULT_TENANT = TenantId("default")
ALT_TENANT = TenantId("alt")


def _ctx(value: int, tenant: TenantId = DEFAULT_TENANT) -> ReadContext:
    return ReadContext(tenant, Revision(value))


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
    def test_get_set_by_object_is_tenant_and_revision_scoped(self) -> None:
        cache = LruTupleCache(max_entries=10, ttl_seconds=None)
        t1 = _rt("doc", "1", "viewer", "user", "alice")
        obj = t1.object
        ctx1 = _ctx(1)
        ctx2 = _ctx(2)
        other_tenant_ctx = _ctx(1, ALT_TENANT)

        assert cache.get_by_object(obj, context=ctx1) is None
        assert cache.info()["misses"] == 1

        cache.set_by_object(obj, context=ctx1, tuples=[t1])
        got = cache.get_by_object(obj, context=ctx1)
        assert got == (t1,)

        keys = list(cache._store._entries)
        assert len(keys) == 1
        assert keys[0].tenant_id == str(DEFAULT_TENANT)
        assert keys[0].revision == 1

        with pytest.raises(AttributeError):
            got.append(_rt("doc", "1", "viewer", "user", "bob"))  # type: ignore[attr-defined]

        assert cache.get_by_object(obj, context=ctx2) is None
        assert cache.get_by_object(obj, context=other_tenant_ctx) is None
        assert cache.get_by_object(obj, context=ctx1) == (t1,)

        t_alt = _rt("doc", "1", "viewer", "user", "mallory")
        cache.set_by_object(obj, context=other_tenant_ctx, tuples=[t_alt])
        assert cache.get_by_object(obj, context=ctx1) == (t1,)
        assert cache.get_by_object(obj, context=other_tenant_ctx) == (t_alt,)

    def test_get_set_by_subject_is_tenant_and_revision_scoped(self) -> None:
        cache = LruTupleCache(max_entries=10, ttl_seconds=None)
        t_direct = _rt("doc", "1", "editor", "user", "alice")
        t_anchor = _rt("doc", "1", "editor", "group", "eng", "member")
        ctx1 = _ctx(1)
        ctx2 = _ctx(2)
        other_tenant_ctx = _ctx(1, ALT_TENANT)

        cache.set_by_subject(
            t_direct.subject,
            context=ctx1,
            tuples=[t_direct],
        )
        assert cache.get_by_subject(
            t_direct.subject,
            context=ctx1,
        ) == (t_direct,)
        assert cache.get_by_subject(t_direct.subject, context=ctx2) is None
        assert cache.get_by_subject(t_direct.subject, context=other_tenant_ctx) is None

        t_alt = _rt("doc", "alt", "editor", "user", "alice")
        cache.set_by_subject(t_direct.subject, context=other_tenant_ctx, tuples=[t_alt])
        assert cache.get_by_subject(t_direct.subject, context=ctx1) == (t_direct,)
        assert cache.get_by_subject(
            t_direct.subject,
            context=other_tenant_ctx,
        ) == (t_alt,)

        key_scopes = {(key.tenant_id, key.revision) for key in cache._store._entries}
        assert (str(DEFAULT_TENANT), 1) in key_scopes
        assert (str(ALT_TENANT), 1) in key_scopes

        cache.set_by_subject(
            t_anchor.subject,
            context=ctx1,
            tuples=[t_anchor],
        )
        assert cache.get_by_subject(
            t_anchor.subject,
            context=ctx1,
        ) == (t_anchor,)

    def test_ttl_expiry_zero_expires_immediately(self) -> None:
        cache = LruTupleCache(max_entries=10, ttl_seconds=0)
        t1 = _rt("doc", "1", "viewer", "user", "alice")
        ctx1 = _ctx(1)
        cache.set_by_object(t1.object, context=ctx1, tuples=[t1])

        assert cache.get_by_object(t1.object, context=ctx1) is None

    def test_ttl_expiry_with_time_advance(self, monkeypatch) -> None:
        import zanzipy.storage.cache.concrete._lru as lru_module

        now = 1000.0

        def fake_monotonic() -> float:
            return now

        monkeypatch.setattr(lru_module.time, "monotonic", fake_monotonic)
        cache = LruTupleCache(max_entries=10, ttl_seconds=5)
        t1 = _rt("doc", "1", "viewer", "user", "alice")
        ctx1 = _ctx(1)

        cache.set_by_object(t1.object, context=ctx1, tuples=[t1])
        assert cache.get_by_object(t1.object, context=ctx1) == (t1,)

        now = 1006.0
        assert cache.get_by_object(t1.object, context=ctx1) is None

    def test_lru_eviction_is_global_across_revisioned_entries(self) -> None:
        cache = LruTupleCache(max_entries=3, ttl_seconds=None)
        t_a = _rt("doc", "A", "viewer", "user", "a")
        t_b = _rt("doc", "B", "viewer", "user", "b")
        t_subject = _rt("doc", "S", "viewer", "user", "s")
        t_c = _rt("doc", "C", "viewer", "user", "c")
        ctx1 = _ctx(1)

        cache.set_by_object(t_a.object, context=ctx1, tuples=[t_a])
        cache.set_by_subject(
            t_subject.subject,
            context=ctx1,
            tuples=[t_subject],
        )
        cache.set_by_object(t_b.object, context=ctx1, tuples=[t_b])

        assert cache.get_by_object(t_a.object, context=ctx1) == (t_a,)
        cache.set_by_object(t_c.object, context=ctx1, tuples=[t_c])

        assert cache.get_by_subject(t_subject.subject, context=ctx1) is None
        assert cache.get_by_object(t_a.object, context=ctx1) == (t_a,)
        assert cache.get_by_object(t_b.object, context=ctx1) == (t_b,)
        assert cache.get_by_object(t_c.object, context=ctx1) == (t_c,)

        info = cache.info()
        assert info["size"] == 3
        assert info["size_objects"] == 3
        assert info["size_subjects"] == 0

    def test_counters_and_lifecycle(self) -> None:
        cache = LruTupleCache(max_entries=5, ttl_seconds=None)
        t1 = _rt("doc", "1", "viewer", "user", "alice")
        ctx1 = _ctx(1)

        assert cache.get_by_object(t1.object, context=ctx1) is None
        cache.set_by_object(t1.object, context=ctx1, tuples=[t1])
        assert cache.get_by_object(t1.object, context=ctx1) == (t1,)

        info = cache.info()
        assert isinstance(info["hits"], int)
        assert info["hits"] >= 1
        assert isinstance(info["misses"], int)
        assert info["misses"] >= 1

        assert cache.ping() is True
        cache.close()
        info2 = cache.info()
        assert info2["size"] == 0
        assert info2["size_objects"] == 0
        assert info2["size_subjects"] == 0

    def test_invalid_capacity_or_ttl_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_entries"):
            LruTupleCache(max_entries=-1)

        with pytest.raises(ValueError, match="ttl_seconds"):
            LruTupleCache(ttl_seconds=-1)
