from zanzipy.schema.rules import RewriteRule
from zanzipy.storage.cache.concrete.redis_rules import (
    DefaultRedisCompiledRuleCodec,
    RedisCompiledRuleCache,
)


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self._data.get(key)

    def set(self, key: str, value: bytes | str, ex: int | None = None) -> bool:
        b = value if isinstance(value, (bytes, bytearray)) else str(value).encode()
        self._data[key] = b
        return True

    def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                count += 1
        return count

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        self._data.clear()


class _Codec(DefaultRedisCompiledRuleCodec):
    prefix: str = "test:cr"


def test_redis_compiled_rule_cache_roundtrip() -> None:
    client = _FakeRedis()
    cache = RedisCompiledRuleCache(client=client, ttl_seconds=None, codec=_Codec())

    # Minimal rule represented as direct
    rule = RewriteRule.from_dict({"type": "direct"})

    assert cache.get("ns", "r") is None
    cache.set("ns", "r", rule)
    got = cache.get("ns", "r")
    assert isinstance(got, RewriteRule)
    assert got.to_dict() == {"type": "direct"}

    cache.invalidate("ns", "r")
    assert cache.get("ns", "r") is None


def test_redis_compiled_rule_cache_invalid_data() -> None:
    client = _FakeRedis()
    codec = _Codec()
    bad_key = codec.key_for_rule("ns", "bad")
    # Write invalid json
    client.set(bad_key, b"not-json")

    cache = RedisCompiledRuleCache(client=client, ttl_seconds=None, codec=codec)
    assert cache.get("ns", "bad") is None
    # Should have deleted the bad key
    assert client.get(bad_key) is None
