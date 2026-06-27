from zanzipy.engine.authorization import AuthorizationEngine
from zanzipy.models import CheckRequest, RelationTuple, Subject
from zanzipy.schema.compiled import CompiledAuthorizationModel
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule, TupleToUsersetRule, UnionRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.cache.concrete.lru import LruTupleCache
from zanzipy.storage.cache.concrete.lru_rules import LruCompiledRuleCache
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository
from zanzipy.storage.revision import ReadContext, TenantId, TupleMutation, WriteContext

DEFAULT_TENANT = TenantId("default")


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


def _member_namespace(name: str) -> NamespaceDef:
    return NamespaceDef(
        name=name,
        relations=(
            RelationDef.with_subjects(
                "member",
                (SubjectReference.from_dict({"namespace": "user"}),),
            ),
        ),
    )


def _viewable_namespace(name: str) -> NamespaceDef:
    return NamespaceDef(
        name=name,
        relations=(
            RelationDef.with_subjects(
                "viewer",
                (SubjectReference.from_dict({"namespace": "user"}),),
            ),
        ),
    )


def _lookup_document_namespace(
    *,
    userset_namespace: str,
    parent_namespace: str,
) -> NamespaceDef:
    return NamespaceDef(
        name="document",
        relations=(
            RelationDef.with_subjects(
                "viewer",
                (
                    SubjectReference.from_dict(
                        {"namespace": userset_namespace, "relation": "member"}
                    ),
                ),
            ),
            RelationDef.with_subjects(
                "parent",
                (SubjectReference.from_dict({"namespace": parent_namespace}),),
            ),
        ),
        permissions=(
            PermissionDef(
                "can_view",
                UnionRule(
                    (
                        ComputedUsersetRule("viewer"),
                        TupleToUsersetRule("parent", "viewer"),
                    )
                ),
            ),
        ),
    )


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


def test_authorization_engine_shares_compiled_model_across_operations() -> None:
    registry = _registry()
    repo = _repo_with_owner()
    cache = LruCompiledRuleCache(max_entries=10, ttl_seconds=None)
    cached = ComputedUsersetRule("owner")
    cache.set("document", "can_view", cached)
    engine = AuthorizationEngine(
        relations_repository=repo,
        schema=registry,
        compiled_rules_cache=cache,
    )
    context = _context(repo)

    assert engine.authorization_model.resolve("document", "can_view") is cached
    assert engine._checker._model is engine.authorization_model
    assert engine._expander._model is engine.authorization_model
    assert engine._lookup._model is engine.authorization_model

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

    assert response.allowed is True
    assert expanded.users == {"user:alice"}
    assert [str(resource) for resource in resources] == ["document:doc1"]
    assert engine.authorization_model.resolve("document", "can_view") is cached


def test_authorization_engine_rebuild_refreshes_lookup_metadata() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        (
            _member_namespace("group"),
            _member_namespace("team"),
            _viewable_namespace("folder"),
            _viewable_namespace("project"),
            _lookup_document_namespace(
                userset_namespace="group",
                parent_namespace="folder",
            ),
        )
    )
    old_model = CompiledAuthorizationModel.from_schema(registry)

    repo = InMemoryRelationRepository()
    repo.write(
        WriteContext(DEFAULT_TENANT),
        tuple(
            TupleMutation.touch(RelationTuple.from_string(tuple_))
            for tuple_ in (
                "group:old#member@user:alice",
                "team:new#member@user:alice",
                "document:group-doc#viewer@group:old#member",
                "document:team-doc#viewer@team:new#member",
                "folder:old#viewer@user:alice",
                "project:new#viewer@user:alice",
                "document:folder-doc#parent@folder:old",
                "document:project-doc#parent@project:new",
            )
        ),
    )
    old_engine = AuthorizationEngine(
        relations_repository=repo,
        schema=registry,
        authorization_model=old_model,
    )
    context = _context(repo)

    registry.update_namespace(
        _lookup_document_namespace(
            userset_namespace="team",
            parent_namespace="project",
        )
    )

    old_resources = old_engine.lookup_resources(
        resource_type="document",
        permission="can_view",
        subject=Subject.from_string("user:alice"),
        context=context,
    )
    rebuilt_engine = AuthorizationEngine(
        relations_repository=repo,
        schema=registry,
    )
    rebuilt_resources = rebuilt_engine.lookup_resources(
        resource_type="document",
        permission="can_view",
        subject=Subject.from_string("user:alice"),
        context=context,
    )

    assert [str(resource) for resource in old_resources] == [
        "document:folder-doc",
        "document:group-doc",
    ]
    assert [str(resource) for resource in rebuilt_resources] == [
        "document:project-doc",
        "document:team-doc",
    ]
