import json

from zanzipy.models import Obj, RelationTuple, Subject
from zanzipy.storage.cache.concrete.redis import (
    DefaultRedisTupleCodec,
    RedisTupleCache,
    RedisTupleCodec,
)


def _rt(s: str) -> RelationTuple:
    # Helper: parse canonical tuple string
    return RelationTuple.from_string(s)


class TestDefaultRedisTupleCodec:
    def test_keys_and_json_roundtrip(self) -> None:
        codec = DefaultRedisTupleCodec(prefix="test:rt")
        obj = Obj.from_string("doc:123")
        subj_direct = Subject.from_string("user:alice")
        subj_anchor = Subject.from_string("group:eng#member")

        assert codec.key_for_object(obj) == "test:rt:obj:doc:123"
        assert codec.key_for_subject(subj_direct) == "test:rt:subj:user:alice:-"
        assert codec.key_for_subject(subj_anchor) == "test:rt:subj:group:eng:member"

        tuples = [_rt("doc:123#viewer@user:alice"), _rt("doc:123#editor@user:bob")]
        payload = codec.encode(tuples)
        # Should be valid JSON list of strings
        raw = json.loads(payload)
        assert isinstance(raw, list)
        assert raw == [str(t) for t in tuples]

        back = codec.decode(payload)
        assert [str(t) for t in back] == [str(t) for t in tuples]


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self.calls: list[tuple[str, tuple, dict]] = []

    def get(self, key: str) -> bytes | None:
        self.calls.append(("get", (key,), {}))
        return self._store.get(key)

    def set(self, key: str, value: bytes | str, ex: int | None = None) -> bool:
        self.calls.append(("set", (key, value), {"ex": ex}))
        if isinstance(value, str):
            value = value.encode("utf-8")
        self._store[key] = value
        return True

    def delete(self, *keys: str) -> int:
        self.calls.append(("delete", (keys,), {}))
        deleted = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
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

    def key_for_object(self, obj: Obj) -> str:
        k = f"k:obj:{obj.namespace}:{obj.id}"
        self.keys.append(k)
        return k

    def key_for_subject(self, subject: Subject) -> str:
        rel = "-" if subject.relation is None else str(subject.relation)
        k = f"k:subj:{subject.namespace}:{subject.id}:{rel}"
        self.keys.append(k)
        return k

    def encode(self, tuples: list[RelationTuple]) -> str:
        data = [str(t) for t in tuples]
        self.encoded.append(data)
        return json.dumps(data)

    def decode(self, data: bytes | str) -> list[RelationTuple]:
        self.decoded_payloads.append(data)
        s = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        return [RelationTuple.from_string(x) for x in json.loads(s)]


class TestRedisTupleCache:
    def test_read_write_invalidate_roundtrip(self) -> None:
        fake = _FakeRedis()
        codec = _TrackingCodec()
        cache = RedisTupleCache(client=fake, ttl_seconds=10, codec=codec)

        obj = Obj.from_string("doc:1")
        subj = Subject.from_string("user:alice")
        tuples = [
            _rt("doc:1#viewer@user:alice"),
            _rt("doc:1#editor@user:bob"),
        ]

        # Misses first
        assert cache.get_by_object(obj) is None
        assert cache.get_by_subject(subj) is None

        # Set entries and read back
        cache.set_by_object(obj, tuples)
        ob = cache.get_by_object(obj)
        assert ob is not None
        assert [str(t) for t in ob] == [str(t) for t in tuples]

        cache.set_by_subject(subj, tuples)
        sb = cache.get_by_subject(subj)
        assert sb is not None
        assert [str(t) for t in sb] == [str(t) for t in tuples]

        # Invalidate
        cache.invalidate_object(obj)
        assert cache.get_by_object(obj) is None
        cache.invalidate_subject(subj)
        assert cache.get_by_subject(subj) is None

        # ping/close pass through
        assert cache.ping() is True
        cache.close()

        # Ensure codec and client interactions happened
        assert any(name == "set" for name, *_ in fake.calls)
        assert any(name == "get" for name, *_ in fake.calls)
        assert any(name == "delete" for name, *_ in fake.calls)
        assert any(name == "ping" for name, *_ in fake.calls)
        assert any(name == "close" for name, *_ in fake.calls)
        assert len(codec.keys) > 0
        assert len(codec.encoded) > 0

    def test_corrupted_payload_clears_key(self) -> None:
        fake = _FakeRedis()
        codec = DefaultRedisTupleCodec(prefix="t:rt")
        cache = RedisTupleCache(client=fake, ttl_seconds=5, codec=codec)

        obj = Obj.from_string("doc:oops")
        subj = Subject.from_string("user:oops")

        # Inject corrupted (non-JSON) payload for object
        obj_key = codec.key_for_object(obj)
        fake._store[obj_key] = b"not-json"
        res_obj = cache.get_by_object(obj)
        assert res_obj is None
        assert obj_key not in fake._store

        # Inject corrupted payload for subject
        subj_key = codec.key_for_subject(subj)
        fake._store[subj_key] = b'["bad-tuple-format"]'
        # Decode will fail when parsing RelationTuple
        res_subj = cache.get_by_subject(subj)
        assert res_subj is None
        assert subj_key not in fake._store

    def test_ttl_passed_to_set_and_none_skips(self) -> None:
        # Case 1: ttl set -> ex propagated
        fake1 = _FakeRedis()
        cache1 = RedisTupleCache(client=fake1, ttl_seconds=7)
        obj = Obj.from_string("doc:ttl")
        subj = Subject.from_string("user:ttl")
        tuples = [_rt("doc:ttl#viewer@user:ttl")]
        cache1.set_by_object(obj, tuples)
        cache1.set_by_subject(subj, tuples)
        set_calls_ex = [
            kwargs.get("ex") for name, _args, kwargs in fake1.calls if name == "set"
        ]
        assert set_calls_ex == [7, 7]

        # Case 2: ttl None -> ex is None
        fake2 = _FakeRedis()
        cache2 = RedisTupleCache(client=fake2, ttl_seconds=None)
        cache2.set_by_object(obj, tuples)
        cache2.set_by_subject(subj, tuples)
        set_calls_ex2 = [
            kwargs.get("ex") for name, _args, kwargs in fake2.calls if name == "set"
        ]
        assert set_calls_ex2 == [None, None]
