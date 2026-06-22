from time import sleep

from zanzipy.storage.cache.concrete.lru_rules import LruCompiledRuleCache


class TestLruCompiledRuleCache:
    def test_get_set_and_invalidate(self) -> None:
        cache = LruCompiledRuleCache[object](max_entries=10, ttl_seconds=None)
        assert cache.get("ns", "a") is None
        cache.set("ns", "a", {"x": 1})
        assert cache.get("ns", "a") == {"x": 1}
        cache.invalidate("ns", "a")
        assert cache.get("ns", "a") is None

    def test_eviction_order(self) -> None:
        cache = LruCompiledRuleCache[int](max_entries=2, ttl_seconds=None)
        cache.set("ns", "a", 1)
        cache.set("ns", "b", 2)
        # Touch "a" to make "b" the least recently used
        assert cache.get("ns", "a") == 1
        cache.set("ns", "c", 3)
        # "b" should be evicted
        assert cache.get("ns", "b") is None
        assert cache.get("ns", "a") == 1
        assert cache.get("ns", "c") == 3

    def test_ttl_expiration(self) -> None:
        cache = LruCompiledRuleCache[str](max_entries=10, ttl_seconds=0.01)
        cache.set("ns", "k", "v")
        assert cache.get("ns", "k") == "v"
        sleep(0.02)
        # entry should expire
        assert cache.get("ns", "k") is None

    def test_invalidate_namespace(self) -> None:
        cache = LruCompiledRuleCache[int](max_entries=10, ttl_seconds=None)
        cache.set("ns1", "a", 1)
        cache.set("ns1", "b", 2)
        cache.set("ns2", "a", 3)
        cache.invalidate_namespace("ns1")
        assert cache.get("ns1", "a") is None
        assert cache.get("ns1", "b") is None
        assert cache.get("ns2", "a") == 3
