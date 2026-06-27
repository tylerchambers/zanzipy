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
    def key_for_object(self, obj: Obj, *, revision: Revision) -> str: ...
    def key_for_subject(self, subject: Subject, *, revision: Revision) -> str: ...
    def encode(self, tuples: Sequence[RelationTuple]) -> bytes | str: ...
    def decode(self, data: bytes | str) -> list[RelationTuple]: ...


@dataclass(frozen=True, slots=True)
class DefaultRedisTupleCodec:
    prefix: str = "z:rt"

    def key_for_object(self, obj: Obj, *, revision: Revision) -> str:
        return f"{self.prefix}:obj:{obj.namespace}:{obj.id}:rev:{revision.value}"

    def key_for_subject(self, subject: Subject, *, revision: Revision) -> str:
        rel = "-" if subject.relation is None else str(subject.relation)
        return (
            f"{self.prefix}:subj:{subject.namespace}:{subject.id}:"
            f"{rel}:rev:{revision.value}"
        )

    def encode(self, tuples: Sequence[RelationTuple]) -> str:
        return json.dumps([str(t) for t in tuples])

    def decode(self, data: bytes | str) -> list[RelationTuple]:
        from zanzipy.models import RelationTuple as RT

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

    def get_by_object(
        self,
        obj: Obj,
        *,
        revision: Revision,
    ) -> Sequence[RelationTuple] | None:
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
        ex = None if self._ttl is None else int(self._ttl)
        token = self._codec.key_for_object(obj, revision=revision)
        self._client.set(token, self._codec.encode(tuples), ex=ex)

    def get_by_subject(
        self,
        subject: Subject,
        *,
        revision: Revision,
    ) -> Sequence[RelationTuple] | None:
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
        ex = None if self._ttl is None else int(self._ttl)
        key = self._codec.key_for_subject(subject, revision=revision)
        self._client.set(key, self._codec.encode(tuples), ex=ex)

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
