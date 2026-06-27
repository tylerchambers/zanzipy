"""Redis-backed cache for revisioned relation tuple buckets."""

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Protocol

from zanzipy.storage.cache.abstract.tuples import TupleCache

if TYPE_CHECKING:
    from collections.abc import Sequence

    from zanzipy.models import Obj, RelationTuple, Subject
    from zanzipy.storage.common import RedisLike
    from zanzipy.storage.revision import Revision


class RedisTupleCodec(Protocol):
    """Serialize tuple cache keys and values for Redis storage."""

    def key_for_object(self, obj: Obj, *, revision: Revision) -> str:
        """Build the Redis key for an object tuple bucket revision."""
        ...

    def key_for_subject(self, subject: Subject, *, revision: Revision) -> str:
        """Build the Redis key for a subject tuple bucket revision."""
        ...

    def encode(self, tuples: Sequence[RelationTuple]) -> bytes | str:
        """Serialize relation tuples into a Redis-compatible value."""
        ...

    def decode(self, data: bytes | str) -> list[RelationTuple]:
        """Deserialize a Redis value into relation tuples."""
        ...


@dataclass(frozen=True, slots=True)
class DefaultRedisTupleCodec:
    """Default string-key and JSON-list codec for relation tuple buckets."""

    prefix: str = "z:rt"

    def key_for_object(self, obj: Obj, *, revision: Revision) -> str:
        """Build an object key from prefix, object identity, and revision."""
        return f"{self.prefix}:obj:{obj.namespace}:{obj.id}:rev:{revision.value}"

    def key_for_subject(self, subject: Subject, *, revision: Revision) -> str:
        """Build a subject key, using ``-`` for a wildcard relation."""
        rel = "-" if subject.relation is None else str(subject.relation)
        return (
            f"{self.prefix}:subj:{subject.namespace}:{subject.id}:"
            f"{rel}:rev:{revision.value}"
        )

    def encode(self, tuples: Sequence[RelationTuple]) -> str:
        """Encode tuples as a JSON array of relation tuple strings."""
        return json.dumps([str(t) for t in tuples])

    def decode(self, data: bytes | str) -> list[RelationTuple]:
        """Decode JSON tuple strings back into ``RelationTuple`` objects."""
        from zanzipy.models import RelationTuple as RT

        s = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        items = json.loads(s)
        return [RT.from_string(x) for x in items]


class RedisTupleCache(TupleCache):
    """Redis-backed tuple cache with TTL writes and corrupt-value eviction."""

    def __init__(
        self,
        *,
        client: RedisLike,
        ttl_seconds: int | None = 30,
        codec: RedisTupleCodec | None = None,
    ) -> None:
        """Bind a Redis client, optional TTL, and tuple key/value codec."""
        self._client = client
        self._ttl = ttl_seconds
        self._codec = codec if codec is not None else DefaultRedisTupleCodec()

    def get_by_object(
        self,
        obj: Obj,
        *,
        revision: Revision,
    ) -> Sequence[RelationTuple] | None:
        """Return an object bucket; delete invalid payloads and return ``None``."""
        token = self._codec.key_for_object(obj, revision=revision)
        raw = self._client.get(token)
        if raw is None:
            return None
        try:
            return self._codec.decode(raw)
        except Exception:
            self._client.delete(token)
            return None

    def set_by_object(
        self,
        obj: Obj,
        *,
        revision: Revision,
        tuples: Sequence[RelationTuple],
    ) -> None:
        """Store an object bucket in Redis using the configured TTL."""
        ex = None if self._ttl is None else int(self._ttl)
        token = self._codec.key_for_object(obj, revision=revision)
        self._client.set(token, self._codec.encode(tuples), ex=ex)

    def get_by_subject(
        self,
        subject: Subject,
        *,
        revision: Revision,
    ) -> Sequence[RelationTuple] | None:
        """Return a subject bucket; delete invalid payloads and return ``None``."""
        token = self._codec.key_for_subject(subject, revision=revision)
        raw = self._client.get(token)
        if raw is None:
            return None
        try:
            return self._codec.decode(raw)
        except Exception:
            self._client.delete(token)
            return None

    def set_by_subject(
        self,
        subject: Subject,
        *,
        revision: Revision,
        tuples: Sequence[RelationTuple],
    ) -> None:
        """Store a subject bucket in Redis using the configured TTL."""
        ex = None if self._ttl is None else int(self._ttl)
        key = self._codec.key_for_subject(subject, revision=revision)
        self._client.set(key, self._codec.encode(tuples), ex=ex)

    def ping(self) -> bool:
        """Return whether the Redis client reports a healthy connection."""
        return bool(self._client.ping())

    def close(self) -> None:
        """Close the owned Redis client connection."""
        self._client.close()

    def info(self) -> dict[str, object]:
        """Return backend, TTL, and codec diagnostics."""
        return {
            "backend": "redis",
            "ttl_seconds": self._ttl,
            "codec": type(self._codec).__name__,
        }
