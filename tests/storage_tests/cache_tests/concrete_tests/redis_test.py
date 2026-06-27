import json

from zanzipy.models import Obj, RelationTuple, Subject
from zanzipy.storage.cache.concrete.redis import (
    DefaultRedisTupleCodec,
    RedisTupleCache,
    RedisTupleCodec,
)
from zanzipy.storage.common import RedisLike
from zanzipy.storage.revision import ReadContext, Revision, TenantId

DEFAULT_TENANT = TenantId("default")
ALT_TENANT = TenantId("alt")


def _ctx(value: int, tenant: TenantId = DEFAULT_TENANT) -> ReadContext:
    return ReadContext(tenant, Revision(value))


def _rt(s: str) -> RelationTuple:
    return RelationTuple.from_string(s)


class TestDefaultRedisTupleCodec:
    def test_keys_include_tenant_revision_and_json_roundtrip(self) -> None:
        codec = DefaultRedisTupleCodec(prefix="test:rt")
        obj = Obj.from_string("doc:123")
        subj_direct = Subject.from_string("user:alice")
        subj_anchor = Subject.from_string("group:eng#member")
        context = _ctx(7)

        assert (
            codec.key_for_object(obj, context=context)
            == "test:rt:tenant:default:obj:doc:123:rev:7"
        )
        assert (
            codec.key_for_subject(subj_direct, context=context)
            == "test:rt:tenant:default:subj:user:alice:-:rev:7"
        )
        assert (
            codec.key_for_subject(subj_anchor, context=context)
            == "test:rt:tenant:default:subj:group:eng:member:rev:7"
        )

        tuples = [_rt("doc:123#viewer@user:alice"), _rt("doc:123#editor@user:bob")]
        payload = codec.encode(tuples)
        raw = json.loads(payload)
        assert isinstance(raw, list)
        assert raw == [str(t) for t in tuples]

        back = codec.decode(payload)
        assert [str(t) for t in back] == [str(t) for t in tuples]


class _FakeRedis(RedisLike):
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def get(self, token: str) -> bytes | None:
        self.calls.append(("get", (token,), {}))
        return self._store.get(token)

    def set(self, key: str, value: bytes | str, ex: int | None = None) -> bool:
        self.calls.append(("set", (key, value), {"ex": ex}))
        if isinstance(value, str):
            value = value.encode("utf-8")
        self._store[key] = value
        return True

    def delete(self, *keys: str) -> int:
        self.calls.append(("delete", (keys,), {}))
        deleted = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                deleted += 1
        return deleted

    def ping(self) -> bool:
        self.calls.append(("ping", (), {}))
        return True

    def close(self) -> None:
        self.calls.append(("close", (), {}))
        return None


class _TrackingCodec(RedisTupleCodec):
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.encoded: list[list[str]] = []
        self.decoded_payloads: list[bytes | str] = []

    def key_for_object(self, obj: Obj, *, context: ReadContext) -> str:
        key = (
            f"k:tenant:{context.tenant}:obj:{obj.namespace}:{obj.id}:"
            f"rev:{context.revision.value}"
        )
        self.keys.append(key)
        return key

    def key_for_subject(self, subject: Subject, *, context: ReadContext) -> str:
        rel = "-" if subject.relation is None else str(subject.relation)
        key = (
            f"k:tenant:{context.tenant}:subj:{subject.namespace}:"
            f"{subject.id}:{rel}:rev:{context.revision.value}"
        )
        self.keys.append(key)
        return key

    def encode(self, tuples: list[RelationTuple]) -> str:
        data = [str(t) for t in tuples]
        self.encoded.append(data)
        return json.dumps(data)

    def decode(self, data: bytes | str) -> list[RelationTuple]:
        self.decoded_payloads.append(data)
        s = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        return [RelationTuple.from_string(x) for x in json.loads(s)]


class TestRedisTupleCache:
    def test_tenant_and_revision_scoped_read_write_roundtrip(self) -> None:
        fake = _FakeRedis()
        codec = _TrackingCodec()
        cache = RedisTupleCache(client=fake, ttl_seconds=10, codec=codec)

        obj = Obj.from_string("doc:1")
        subj = Subject.from_string("user:alice")
        tuples = [
            _rt("doc:1#viewer@user:alice"),
            _rt("doc:1#editor@user:bob"),
        ]
        ctx1 = _ctx(1)
        ctx2 = _ctx(2)
        other_tenant_ctx = _ctx(1, ALT_TENANT)

        assert cache.get_by_object(obj, context=ctx1) is None
        assert cache.get_by_subject(subj, context=ctx1) is None

        cache.set_by_object(obj, context=ctx1, tuples=tuples)
        ob = cache.get_by_object(obj, context=ctx1)
        assert ob is not None
        assert [str(t) for t in ob] == [str(t) for t in tuples]
        assert cache.get_by_object(obj, context=ctx2) is None
        assert cache.get_by_object(obj, context=other_tenant_ctx) is None

        cache.set_by_subject(subj, context=ctx1, tuples=tuples)
        sb = cache.get_by_subject(subj, context=ctx1)
        assert sb is not None
        assert [str(t) for t in sb] == [str(t) for t in tuples]
        assert cache.get_by_subject(subj, context=ctx2) is None
        assert cache.get_by_subject(subj, context=other_tenant_ctx) is None

        alt_object_tuples = [_rt("doc:1#viewer@user:mallory")]
        alt_subject_tuples = [_rt("doc:alt#viewer@user:alice")]
        cache.set_by_object(obj, context=other_tenant_ctx, tuples=alt_object_tuples)
        cache.set_by_subject(subj, context=other_tenant_ctx, tuples=alt_subject_tuples)
        assert [str(t) for t in cache.get_by_object(obj, context=ctx1) or ()] == [
            str(t) for t in tuples
        ]
        assert [
            str(t) for t in cache.get_by_object(obj, context=other_tenant_ctx) or ()
        ] == [str(t) for t in alt_object_tuples]
        assert [str(t) for t in cache.get_by_subject(subj, context=ctx1) or ()] == [
            str(t) for t in tuples
        ]
        assert [
            str(t) for t in cache.get_by_subject(subj, context=other_tenant_ctx) or ()
        ] == [str(t) for t in alt_subject_tuples]

        assert "k:tenant:default:obj:doc:1:rev:1" in codec.keys
        assert "k:tenant:default:subj:user:alice:-:rev:1" in codec.keys
        assert "k:tenant:alt:obj:doc:1:rev:1" in codec.keys
        assert "k:tenant:alt:subj:user:alice:-:rev:1" in codec.keys

        assert cache.ping() is True
        cache.close()

        assert any(name == "set" for name, *_ in fake.calls)
        assert any(name == "get" for name, *_ in fake.calls)
        assert any(name == "ping" for name, *_ in fake.calls)
        assert any(name == "close" for name, *_ in fake.calls)
        assert len(codec.keys) > 0
        assert len(codec.encoded) > 0

    def test_corrupted_payload_clears_context_key(self) -> None:
        fake = _FakeRedis()
        codec = DefaultRedisTupleCodec(prefix="t:rt")
        cache = RedisTupleCache(client=fake, ttl_seconds=5, codec=codec)

        obj = Obj.from_string("doc:oops")
        subj = Subject.from_string("user:oops")
        context = _ctx(3)

        obj_key = codec.key_for_object(obj, context=context)
        fake._store[obj_key] = b"not-json"
        res_obj = cache.get_by_object(obj, context=context)
        assert res_obj is None
        assert obj_key not in fake._store

        subj_key = codec.key_for_subject(subj, context=context)
        fake._store[subj_key] = b'["bad-tuple-format"]'
        res_subj = cache.get_by_subject(subj, context=context)
        assert res_subj is None
        assert subj_key not in fake._store

    def test_ttl_passed_to_set_and_none_skips(self) -> None:
        fake1 = _FakeRedis()
        cache1 = RedisTupleCache(client=fake1, ttl_seconds=7)
        obj = Obj.from_string("doc:ttl")
        subj = Subject.from_string("user:ttl")
        tuples = [_rt("doc:ttl#viewer@user:ttl")]
        context = _ctx(1)
        cache1.set_by_object(obj, context=context, tuples=tuples)
        cache1.set_by_subject(subj, context=context, tuples=tuples)
        set_calls_ex = [
            kwargs.get("ex") for name, _args, kwargs in fake1.calls if name == "set"
        ]
        assert set_calls_ex == [7, 7]

        fake2 = _FakeRedis()
        cache2 = RedisTupleCache(client=fake2, ttl_seconds=None)
        cache2.set_by_object(obj, context=context, tuples=tuples)
        cache2.set_by_subject(subj, context=context, tuples=tuples)
        set_calls_ex2 = [
            kwargs.get("ex") for name, _args, kwargs in fake2.calls if name == "set"
        ]
        assert set_calls_ex2 == [None, None]
