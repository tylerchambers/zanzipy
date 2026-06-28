from zanzipy.models import Obj, RelationTuple, Subject
from zanzipy.storage.cache.abstract.rules import CompiledRuleCache
from zanzipy.storage.cache.abstract.tuples import TupleCache
from zanzipy.storage.revision import ReadContext, Revision, TenantId


class _RuleCache(CompiledRuleCache[object]):
    def get(self, namespace: str, name: str) -> object | None:
        return None

    def set(self, namespace: str, name: str, compiled: object) -> None:
        return None

    def invalidate(self, namespace: str, name: str) -> None:
        return None

    def invalidate_namespace(self, namespace: str) -> None:
        return None


class _TupleCache(TupleCache):
    def get_by_object(
        self,
        obj: Obj,
        *,
        context: ReadContext,
    ) -> tuple[RelationTuple, ...] | None:
        return None

    def set_by_object(
        self,
        obj: Obj,
        *,
        context: ReadContext,
        tuples: tuple[RelationTuple, ...],
    ) -> None:
        return None

    def get_by_subject(
        self,
        subject: Subject,
        *,
        context: ReadContext,
    ) -> tuple[RelationTuple, ...] | None:
        return None

    def set_by_subject(
        self,
        subject: Subject,
        *,
        context: ReadContext,
        tuples: tuple[RelationTuple, ...],
    ) -> None:
        return None


class TestAbstractCacheDefaults:
    def test_compiled_rule_cache_defaults_are_healthy_noops(self) -> None:
        cache = _RuleCache()

        assert cache.ping() is True
        assert cache.close() is None
        assert cache.info() == {}

    def test_tuple_cache_defaults_are_healthy_noops(self) -> None:
        cache = _TupleCache()
        context = ReadContext(TenantId("default"), Revision(0))

        assert (
            cache.get_by_object(Obj.from_string("document:doc1"), context=context)
            is None
        )
        assert (
            cache.get_by_subject(Subject.from_string("user:alice"), context=context)
            is None
        )
        assert cache.ping() is True
        assert cache.close() is None
        assert cache.info() == {}
