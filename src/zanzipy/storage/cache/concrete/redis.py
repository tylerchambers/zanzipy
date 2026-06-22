"""Redis-backed cache for relation tuples with injectable codec and client.

This avoids hard runtime deps by accepting any redis-like client with `get`,
`set`, `delete`, and optional `ping`. A pluggable codec controls key format and
serialization for easy customization and testing.
"""

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Protocol

from zanzipy.storage.cache.abstract.tuples import TupleCache

if TYPE_CHECKING:
    from collections.abc import Sequence

    from zanzipy.models import Obj, RelationTuple, Subject
    from zanzipy.storage.common import RedisLike


class RedisTupleCodec(Protocol):
    def key_for_object(self, obj: Obj) -> str: ...
    def key_for_subject(self, subject: Subject) -> str: ...
    def encode(self, tuples: Sequence[RelationTuple]) -> bytes | str: ...
    def decode(self, data: bytes | str) -> list[RelationTuple]: ...


@dataclass(frozen=True, slots=True)
class DefaultRedisTupleCodec:
    prefix: str = "z:rt"

    def key_for_object(self, obj: Obj) -> str:
        return f"{self.prefix}:obj:{obj.namespace}:{obj.id}"

    def key_for_subject(self, subject: Subject) -> str:
        rel = "-" if subject.relation is None else str(subject.relation)
        return f"{self.prefix}:subj:{subject.namespace}:{subject.id}:{rel}"

    def encode(self, tuples: Sequence[RelationTuple]) -> str:
        # Store as JSON array of canonical tuple strings
        return json.dumps([str(t) for t in tuples])

    def decode(self, data: bytes | str) -> list[RelationTuple]:
        from zanzipy.models.tuple import RelationTuple as RT

        s = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        items = json.loads(s)
        return [RT.from_string(x) for x in items]


class RedisTupleCache(TupleCache):
    def __init__(
        self,
        *,
        client: RedisLike,
        ttl_seconds: int | None = 30,
        codec: RedisTupleCodec | None = None,
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._codec = codec if codec is not None else DefaultRedisTupleCodec()

    def get_by_object(self, obj: Obj) -> Sequence[RelationTuple] | None:
        raw = self._client.get(self._codec.key_for_object(obj))
        if raw is None:
            return None
        try:
            return self._codec.decode(raw)
        except Exception:
            self._client.delete(self._codec.key_for_object(obj))
            return None

    def set_by_object(self, obj: Obj, tuples: Sequence[RelationTuple]) -> None:
        ex = None if self._ttl is None else int(self._ttl)
        self._client.set(
            self._codec.key_for_object(obj), self._codec.encode(tuples), ex=ex
        )

    def invalidate_object(self, obj: Obj) -> None:
        self._client.delete(self._codec.key_for_object(obj))

    def get_by_subject(self, subject: Subject) -> Sequence[RelationTuple] | None:
        raw = self._client.get(self._codec.key_for_subject(subject))
        if raw is None:
            return None
        try:
            return self._codec.decode(raw)
        except Exception:
            self._client.delete(self._codec.key_for_subject(subject))
            return None

    def set_by_subject(self, subject: Subject, tuples: Sequence[RelationTuple]) -> None:
        ex = None if self._ttl is None else int(self._ttl)
        self._client.set(
            self._codec.key_for_subject(subject), self._codec.encode(tuples), ex=ex
        )

    def invalidate_subject(self, subject: Subject) -> None:
        self._client.delete(self._codec.key_for_subject(subject))

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
