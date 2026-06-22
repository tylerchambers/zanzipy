"""Redis-backed cache for compiled rewrite rules.

Follows the pattern used by RedisTupleCache, but stores compiled RewriteRule
objects (or downstream-compiled plans) under keys keyed by (namespace, name).
"""

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Protocol

from zanzipy.schema.rules import RewriteRule
from zanzipy.storage.cache.abstract.rules import CompiledRuleCache

if TYPE_CHECKING:
    from zanzipy.storage.common import RedisLike


class RedisCompiledRuleCodec(Protocol):
    def key_for_rule(self, namespace: str, name: str) -> str: ...
    def encode(self, rule: RewriteRule) -> bytes | str: ...
    def decode(self, data: bytes | str) -> RewriteRule: ...


@dataclass(frozen=True, slots=True)
class DefaultRedisCompiledRuleCodec:
    prefix: str = "z:cr"

    def key_for_rule(self, namespace: str, name: str) -> str:
        return f"{self.prefix}:rule:{namespace}:{name}"

    def encode(self, rule: RewriteRule) -> str:
        return json.dumps(rule.to_dict())

    def decode(self, data: bytes | str) -> RewriteRule:
        from zanzipy.schema.rules import RewriteRule as RR

        s = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        return RR.from_dict(json.loads(s))


class RedisCompiledRuleCache(CompiledRuleCache[RewriteRule]):
    def __init__(
        self,
        *,
        client: RedisLike,
        ttl_seconds: int | None = 300,
        codec: RedisCompiledRuleCodec | None = None,
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._codec = codec if codec is not None else DefaultRedisCompiledRuleCodec()

    def get(self, namespace: str, name: str):
        raw = self._client.get(self._codec.key_for_rule(namespace, name))
        if raw is None:
            return None
        try:
            return self._codec.decode(raw)
        except Exception:
            self._client.delete(self._codec.key_for_rule(namespace, name))
            return None

    def set(self, namespace: str, name: str, compiled):
        ex = None if self._ttl is None else int(self._ttl)
        self._client.set(
            self._codec.key_for_rule(namespace, name),
            self._codec.encode(compiled),
            ex=ex,
        )

    def invalidate(self, namespace: str, name: str) -> None:
        self._client.delete(self._codec.key_for_rule(namespace, name))

    def invalidate_namespace(self, namespace: str) -> None:
        # Best-effort: without key scan, we cannot delete by pattern; leave noop.
        # Users can implement a codec with per-namespace tracking if needed.
        return None

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
