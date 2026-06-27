import fnmatch
from typing import TYPE_CHECKING

import pytest

from zanzipy.schema.rules import RewriteRule
from zanzipy.storage.cache.concrete.redis_rules import (
    DefaultRedisCompiledRuleCodec,
    RedisCompiledRuleCache,
    RedisRuleClient,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


class _FakeRedis(RedisRuleClient):
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def get(self, token: str) -> bytes | None:
        return self._data.get(token)

    def set(self, key: str, value: bytes | str, ex: int | None = None) -> bool:
        stored = value if isinstance(value, bytes) else str(value).encode()
        self._data[key] = stored
        return True

    def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                count += 1
        return count

    def scan_iter(self, match: str) -> Iterable[bytes | str]:
        for key in list(self._data):
            if fnmatch.fnmatch(key, match):
                yield key

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        self._data.clear()

    def get_for_test(self, token: str) -> bytes | None:
        return self._data.get(token)


class _Codec(DefaultRedisCompiledRuleCodec):
    prefix: str = "test:cr"


def _rule() -> RewriteRule:
    return RewriteRule.from_dict({"type": "direct"})


def test_redis_compiled_rule_cache_roundtrip() -> None:
    client = _FakeRedis()
    cache = RedisCompiledRuleCache(client=client, ttl_seconds=None, codec=_Codec())

    rule = _rule()

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
    client.set(bad_key, b"not-json")

    cache = RedisCompiledRuleCache(client=client, ttl_seconds=None, codec=codec)
    assert cache.get("ns", "bad") is None
    assert client.get_for_test(bad_key) is None


def test_redis_compiled_rule_cache_invalidates_namespace() -> None:
    client = _FakeRedis()
    cache = RedisCompiledRuleCache(client=client, ttl_seconds=None, codec=_Codec())

    cache.set("ns1", "a", _rule())
    cache.set("ns1", "b", _rule())
    cache.set("ns2", "a", _rule())

    cache.invalidate_namespace("ns1")

    assert cache.get("ns1", "a") is None
    assert cache.get("ns1", "b") is None
    assert cache.get("ns2", "a") is not None


def test_redis_compiled_rule_cache_rejects_non_positive_ttl() -> None:
    client = _FakeRedis()

    with pytest.raises(ValueError, match="ttl_seconds"):
        RedisCompiledRuleCache(client=client, ttl_seconds=0)
