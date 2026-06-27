"""Redis-backed cache for compiled rewrite rules."""

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Protocol

from zanzipy.schema.rules import RewriteRule
from zanzipy.storage.cache.abstract.rules import CompiledRuleCache

if TYPE_CHECKING:
    from collections.abc import Iterable


class RedisRuleClient(Protocol):
    """Redis client surface required by ``RedisCompiledRuleCache``."""

    def get(self, key: str) -> bytes | str | None: ...
    def set(self, key: str, value: bytes | str, ex: int | None = None) -> bool: ...
    def delete(self, *keys: str) -> int: ...
    def scan_iter(self, match: str) -> Iterable[bytes | str]: ...
    def ping(self) -> bool: ...
    def close(self) -> None: ...


class RedisCompiledRuleCodec(Protocol):
    def key_for_rule(self, namespace: str, name: str) -> str: ...
    def key_pattern_for_namespace(self, namespace: str) -> str: ...
    def encode(self, rule: RewriteRule) -> bytes | str: ...
    def decode(self, data: bytes | str) -> RewriteRule: ...


@dataclass(frozen=True, slots=True)
class DefaultRedisCompiledRuleCodec:
    prefix: str = "z:cr"

    def key_for_rule(self, namespace: str, name: str) -> str:
        return f"{self.prefix}:rule:{namespace}:{name}"

    def key_pattern_for_namespace(self, namespace: str) -> str:
        return f"{self.prefix}:rule:{namespace}:*"

    def encode(self, rule: RewriteRule) -> str:
        return json.dumps(rule.to_dict())

    def decode(self, data: bytes | str) -> RewriteRule:
        payload = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        return RewriteRule.from_dict(json.loads(payload))


class RedisCompiledRuleCache(CompiledRuleCache[RewriteRule]):
    """Redis cache for schema ``RewriteRule`` objects.

    Namespace invalidation uses Redis ``SCAN`` via ``scan_iter`` so it matches the
    abstract cache contract without blocking Redis on large keyspaces.
    """

    def __init__(
        self,
        *,
        client: RedisRuleClient,
        ttl_seconds: int | None = 300,
        codec: RedisCompiledRuleCodec | None = None,
    ) -> None:
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive or None")
        self._client = client
        self._ttl = ttl_seconds
        self._codec = codec if codec is not None else DefaultRedisCompiledRuleCodec()

    def get(self, namespace: str, name: str) -> RewriteRule | None:
        token = self._codec.key_for_rule(namespace, name)
        raw = self._client.get(token)
        if raw is None:
            return None
        try:
            return self._codec.decode(raw)
        except Exception:
            self._client.delete(token)
            return None

    def set(self, namespace: str, name: str, compiled: RewriteRule) -> None:
        self._client.set(
            self._codec.key_for_rule(namespace, name),
            self._codec.encode(compiled),
            ex=self._ttl,
        )

    def invalidate(self, namespace: str, name: str) -> None:
        self._client.delete(self._codec.key_for_rule(namespace, name))

    def invalidate_namespace(self, namespace: str) -> None:
        keys = [
            _decode_redis_key(key)
            for key in self._client.scan_iter(
                match=self._codec.key_pattern_for_namespace(namespace)
            )
        ]
        if keys:
            self._client.delete(*keys)

    def ping(self) -> bool:
        return bool(self._client.ping())

    def close(self) -> None:
        self._client.close()

    def info(self) -> dict[str, object]:
        return {
            "backend": "redis",
            "ttl_seconds": self._ttl,
            "codec": type(self._codec).__name__,
        }


def _decode_redis_key(key: bytes | str) -> str:
    return key.decode("utf-8") if isinstance(key, bytes) else key
