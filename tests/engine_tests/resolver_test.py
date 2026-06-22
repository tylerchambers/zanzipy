import pytest

from zanzipy.engine.resolver import RuleResolver
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule, DirectRule, RewriteRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.schema.types import SchemaDefinitionType
from zanzipy.storage.cache.abstract.rules import CompiledRuleCache


class _FakeCache(CompiledRuleCache[RewriteRule]):
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, str, RewriteRule]] = []
        self._store: dict[tuple[str, str], RewriteRule] = {}

    def get(self, namespace: str, name: str) -> RewriteRule | None:
        self.get_calls.append((namespace, name))
        return self._store.get((namespace, name))

    def set(self, namespace: str, name: str, compiled: RewriteRule) -> None:
        self.set_calls.append((namespace, name, compiled))
        self._store[(namespace, name)] = compiled

    def invalidate(self, namespace: str, name: str) -> None:
        self._store.pop((namespace, name), None)

    def invalidate_namespace(self, namespace: str) -> None:
        for key in list(self._store):
            if key[0] == namespace:
                self._store.pop(key, None)

    def prime(self, namespace: str, name: str, compiled: RewriteRule) -> None:
        self._store[(namespace, name)] = compiled


def _registry(view_relation: str = "viewer") -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="doc",
            relations=(
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
                RelationDef.with_subjects(
                    "editor", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(
                PermissionDef(name="view", rewrite=ComputedUsersetRule(view_relation)),
            ),
        )
    )
    return registry


class TestRuleResolver:
    def test_relation_without_rewrite_resolves_to_direct_rule(self) -> None:
        resolver = RuleResolver(schema=_registry())

        rewrite = resolver.resolve("doc", "viewer")

        assert isinstance(rewrite, DirectRule)

    def test_permission_resolves_to_current_rewrite(self) -> None:
        resolver = RuleResolver(schema=_registry())

        rewrite = resolver.resolve("doc", "view")

        assert isinstance(rewrite, ComputedUsersetRule)
        assert rewrite.relation == "viewer"

    def test_matching_cached_rule_is_reused(self) -> None:
        cache = _FakeCache()
        cached = ComputedUsersetRule("viewer")
        cache.prime("doc", "view", cached)
        resolver = RuleResolver(schema=_registry(), compiled_rules_cache=cache)

        rewrite = resolver.resolve("doc", "view")

        assert rewrite is cached
        assert cache.get_calls == [("doc", "view")]
        assert cache.set_calls == []

    def test_stale_cached_rule_is_replaced_from_schema(self) -> None:
        cache = _FakeCache()
        cache.prime("doc", "view", ComputedUsersetRule("viewer"))
        resolver = RuleResolver(
            schema=_registry(view_relation="editor"),
            compiled_rules_cache=cache,
        )

        rewrite = resolver.resolve("doc", "view")

        assert isinstance(rewrite, ComputedUsersetRule)
        assert rewrite.relation == "editor"
        assert cache.set_calls == [("doc", "view", rewrite)]

    def test_permission_without_rewrite_raises(self, monkeypatch) -> None:
        registry = SchemaRegistry()

        def _fake_get_def(self, object_type: str, relation: str) -> dict:  # type: ignore[override]
            return {
                "type": SchemaDefinitionType.PERMISSION,
                "name": relation,
                "rewrite": None,
            }

        monkeypatch.setattr(
            SchemaRegistry,
            "get_relation_definition",
            _fake_get_def,
            raising=False,
        )
        resolver = RuleResolver(schema=registry)

        with pytest.raises(ValueError, match="Permission has no rewrite: doc:view"):
            resolver.resolve("doc", "view")

    def test_unknown_definition_type_raises(self, monkeypatch) -> None:
        registry = SchemaRegistry()

        def _fake_get_def(self, object_type: str, relation: str) -> dict:  # type: ignore[override]
            return {"type": "weird", "name": relation}

        monkeypatch.setattr(
            SchemaRegistry,
            "get_relation_definition",
            _fake_get_def,
            raising=False,
        )
        resolver = RuleResolver(schema=registry)

        with pytest.raises(ValueError, match="Unknown definition type"):
            resolver.resolve("doc", "view")
