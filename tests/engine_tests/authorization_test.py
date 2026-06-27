from zanzipy.engine.authorization import AuthorizationEngine
from zanzipy.models import CheckRequest, RelationTuple, Subject
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule, RewriteRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.cache.abstract.rules import CompiledRuleCache
from zanzipy.storage.cache.concrete.lru import LruTupleCache
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository
from zanzipy.storage.revision import ReadContext, TenantId, TupleMutation, WriteContext

DEFAULT_TENANT = TenantId("default")


class _FakeCompiledRuleCache(CompiledRuleCache[RewriteRule]):
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], RewriteRule] = {}
        self.get_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, str, RewriteRule]] = []

    def get(self, namespace: str, name: str) -> RewriteRule | None:
        self.get_calls.append((namespace, name))
        return self.store.get((namespace, name))

    def set(self, namespace: str, name: str, compiled: RewriteRule) -> None:
        self.set_calls.append((namespace, name, compiled))
        self.store[(namespace, name)] = compiled

    def invalidate(self, namespace: str, name: str) -> None:
        self.store.pop((namespace, name), None)

    def invalidate_namespace(self, namespace: str) -> None:
        for key in tuple(self.store):
            if key[0] == namespace:
                self.store.pop(key)


def _registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "owner",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(PermissionDef("can_view", ComputedUsersetRule("owner")),),
        )
    )
    return registry


def _repo_with_owner() -> InMemoryRelationRepository:
    repo = InMemoryRelationRepository()
    repo.write(
        WriteContext(DEFAULT_TENANT),
        (
            TupleMutation.touch(
                RelationTuple.from_string("document:doc1#owner@user:alice")
            ),
        ),
    )
    return repo


def _context(repo: InMemoryRelationRepository) -> ReadContext:
    return ReadContext(DEFAULT_TENANT, repo.head_revision(DEFAULT_TENANT))


def test_authorization_engine_shares_depth_and_debug_across_operations() -> None:
    registry = _registry()
    repo = _repo_with_owner()
    engine = AuthorizationEngine(
        relations_repository=repo,
        schema=registry,
        max_depth=0,
        enable_debug=True,
    )
    context = _context(repo)

    response = engine.check(
        CheckRequest.from_strings("document:doc1", "can_view", "user:alice"),
        context=context,
    )
    expanded = engine.expand("document", "doc1", "can_view", context=context)
    resources = engine.lookup_resources(
        resource_type="document",
        permission="can_view",
        subject=Subject.from_string("user:alice"),
        context=context,
    )

    assert engine.relations_repository is repo
    assert engine.schema is registry
    assert engine.max_depth == 0
    assert engine.enable_debug is True
    assert response.allowed is False
    assert response.debug_trace is not None
    assert any("Max depth reached" in line for line in response.debug_trace)
    assert expanded.users == set()
    assert expanded.usersets == set()
    assert resources == []


def test_authorization_engine_owns_tuple_cache_decorator() -> None:
    registry = _registry()
    repo = _repo_with_owner()
    cache = LruTupleCache(max_entries=10, ttl_seconds=None)
    engine = AuthorizationEngine(
        relations_repository=repo,
        schema=registry,
        tuple_cache=cache,
    )
    context = _context(repo)
    request = CheckRequest.from_strings("document:doc1", "owner", "user:alice")

    assert engine.relations_repository is not repo
    assert engine.check(request, context=context).allowed is True
    assert engine.check(request, context=context).allowed is True

    after_checks = cache.info()
    check_hits = after_checks["hits"]
    check_misses = after_checks["misses"]
    assert isinstance(check_hits, int)
    assert isinstance(check_misses, int)
    assert check_misses == 1
    assert check_hits == 1

    expanded = engine.expand("document", "doc1", "owner", context=context)
    assert expanded.users == {"user:alice"}
    after_expand = cache.info()
    expand_hits = after_expand["hits"]
    assert isinstance(expand_hits, int)
    assert expand_hits == check_hits + 1

    resources = engine.lookup_resources(
        resource_type="document",
        permission="owner",
        subject=Subject.from_string("user:alice"),
        context=context,
    )
    assert [str(resource) for resource in resources] == ["document:doc1"]

    after_lookup = cache.info()
    subject_bucket_count = after_lookup["size_subjects"]
    lookup_hits = after_lookup["hits"]
    assert isinstance(subject_bucket_count, int)
    assert isinstance(lookup_hits, int)
    assert subject_bucket_count > 0

    resources = engine.lookup_resources(
        resource_type="document",
        permission="owner",
        subject=Subject.from_string("user:alice"),
        context=context,
    )
    final_hits = cache.info()["hits"]
    assert [str(resource) for resource in resources] == ["document:doc1"]
    assert isinstance(final_hits, int)
    assert final_hits > lookup_hits


def test_authorization_engine_reuses_compiled_rule_cache_across_operations() -> None:
    registry = _registry()
    repo = _repo_with_owner()
    cache = _FakeCompiledRuleCache()
    engine = AuthorizationEngine(
        relations_repository=repo,
        schema=registry,
        compiled_rules_cache=cache,
    )
    context = _context(repo)

    response = engine.check(
        CheckRequest.from_strings("document:doc1", "can_view", "user:alice"),
        context=context,
    )
    assert response.allowed is True
    sets_after_check = len(cache.set_calls)

    expanded = engine.expand("document", "doc1", "can_view", context=context)
    resources = engine.lookup_resources(
        resource_type="document",
        permission="can_view",
        subject=Subject.from_string("user:alice"),
        context=context,
    )

    assert expanded.users == {"user:alice"}
    assert [str(resource) for resource in resources] == ["document:doc1"]
    assert len(cache.set_calls) == sets_after_check
    assert cache.get_calls.count(("document", "can_view")) == 3
    assert cache.get_calls.count(("document", "owner")) == 3
