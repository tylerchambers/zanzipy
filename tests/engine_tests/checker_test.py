from zanzipy.engine.checker import CheckEngine
from zanzipy.models.check import CheckRequest
from zanzipy.models.tuple import RelationTuple
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import (
    ComputedUsersetRule,
    ExclusionRule,
    IntersectionRule,
    RewriteRule,
    TupleToUsersetRule,
    UnionRule,
)
from zanzipy.schema.subjects import SubjectReference
from zanzipy.schema.types import SchemaDefinitionType
from zanzipy.storage.repos.concrete.memory.relations import (
    InMemoryRelationRepository,
)


class TestCheckEngine:
    def test_direct_relation(self) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "owner",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(),
        )
        registry.register(ns)

        repo = InMemoryRelationRepository()
        repo.write(RelationTuple.from_string("document:doc1#owner@user:alice"))

        engine = CheckEngine(relations_repository=repo, schema=registry)

        # Positive
        req = CheckRequest.from_strings("document:doc1", "owner", "user:alice")
        res = engine.check(req)
        assert res.allowed is True

        # Negative
        req = CheckRequest.from_strings("document:doc1", "owner", "user:bob")
        res = engine.check(req)
        assert res.allowed is False

    def test_computed_userset_union(self) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "owner",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
                RelationDef.with_subjects(
                    "editor",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
                RelationDef.with_subjects(
                    "viewer",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                    rewrite=UnionRule(
                        children=(
                            ComputedUsersetRule("owner"),
                            ComputedUsersetRule("editor"),
                        )
                    ),
                ),
            ),
            permissions=(
                PermissionDef(name="can_view", rewrite=ComputedUsersetRule("viewer")),
            ),
        )
        registry.register(ns)

        repo = InMemoryRelationRepository()
        repo.write(RelationTuple.from_string("document:doc1#owner@user:alice"))
        repo.write(RelationTuple.from_string("document:doc2#editor@user:carol"))

        engine = CheckEngine(relations_repository=repo, schema=registry)

        # Viewer via owner
        req = CheckRequest.from_strings("document:doc1", "viewer", "user:alice")
        assert engine.check(req).allowed is True

        # Viewer via editor
        req = CheckRequest.from_strings("document:doc2", "viewer", "user:carol")
        assert engine.check(req).allowed is True

        # Permission can_view delegates to viewer
        req = CheckRequest.from_strings("document:doc1", "can_view", "user:alice")
        assert engine.check(req).allowed is True

        # Negative
        req = CheckRequest.from_strings("document:doc2", "viewer", "user:alice")
        assert engine.check(req).allowed is False

    def test_intersection_and_exclusion(self) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "owner",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
                RelationDef.with_subjects(
                    "editor",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
                RelationDef.with_subjects(
                    "viewer",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
                RelationDef.with_subjects(
                    "banned",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(
                PermissionDef(
                    name="can_edit",
                    rewrite=IntersectionRule(
                        children=(
                            ComputedUsersetRule("owner"),
                            ComputedUsersetRule("editor"),
                        )
                    ),
                ),
                PermissionDef(
                    name="can_comment",
                    rewrite=ExclusionRule(
                        base=ComputedUsersetRule("viewer"),
                        subtract=ComputedUsersetRule("banned"),
                    ),
                ),
            ),
        )
        registry.register(ns)

        repo = InMemoryRelationRepository()
        # alice is both owner and editor of doc1 -> can_edit
        repo.write(RelationTuple.from_string("document:doc1#owner@user:alice"))
        repo.write(RelationTuple.from_string("document:doc1#editor@user:alice"))
        # bob is only viewer of doc2 -> can_comment unless banned
        repo.write(RelationTuple.from_string("document:doc2#viewer@user:bob"))
        # carol viewer and banned on doc3 -> cannot comment
        repo.write(RelationTuple.from_string("document:doc3#viewer@user:carol"))
        repo.write(RelationTuple.from_string("document:doc3#banned@user:carol"))

        engine = CheckEngine(relations_repository=repo, schema=registry)

        # Intersection
        req = CheckRequest.from_strings("document:doc1", "can_edit", "user:alice")
        assert engine.check(req).allowed is True
        req = CheckRequest.from_strings("document:doc1", "can_edit", "user:bob")
        assert engine.check(req).allowed is False

        # Exclusion
        req = CheckRequest.from_strings("document:doc2", "can_comment", "user:bob")
        assert engine.check(req).allowed is True
        req = CheckRequest.from_strings("document:doc3", "can_comment", "user:carol")
        assert engine.check(req).allowed is False


    def test_reused_relation_operand_is_path_local(self) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "owner",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(
                PermissionDef(
                    name="owner_twice",
                    rewrite=IntersectionRule(
                        children=(
                            ComputedUsersetRule("owner"),
                            ComputedUsersetRule("owner"),
                        )
                    ),
                ),
                PermissionDef(
                    name="owner_minus_owner",
                    rewrite=ExclusionRule(
                        base=ComputedUsersetRule("owner"),
                        subtract=ComputedUsersetRule("owner"),
                    ),
                ),
            ),
        )
        registry.register(ns)

        repo = InMemoryRelationRepository()
        repo.write(RelationTuple.from_string("document:doc#owner@user:alice"))

        engine = CheckEngine(relations_repository=repo, schema=registry)

        req = CheckRequest.from_strings("document:doc", "owner_twice", "user:alice")
        assert engine.check(req).allowed is True

        req = CheckRequest.from_strings(
            "document:doc", "owner_minus_owner", "user:alice"
        )
        assert engine.check(req).allowed is False

    def test_tuple_to_userset(self) -> None:
        registry = SchemaRegistry()

        # Target namespace: folder.viewer allows users directly
        folder_ns = NamespaceDef(
            name="folder",
            relations=(
                RelationDef.with_subjects(
                    "viewer",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(),
        )

        # Source namespace: document.parent points to folder objects; declare
        # 'viewer' as a relation in document to satisfy validation constraints.
        document_ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "parent",
                    (SubjectReference.from_dict({"namespace": "folder"}),),
                ),
                RelationDef.with_subjects(
                    "viewer",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(
                PermissionDef(
                    name="can_view",
                    rewrite=TupleToUsersetRule(
                        tuple_relation="parent", computed_relation="viewer"
                    ),
                ),
            ),
        )

        registry.register_many([folder_ns, document_ns])

        repo = InMemoryRelationRepository()
        # doc1 -> parent folder f1; folder f1 -> viewer alice
        repo.write(RelationTuple.from_string("document:doc1#parent@folder:f1"))
        repo.write(RelationTuple.from_string("folder:f1#viewer@user:alice"))

        engine = CheckEngine(relations_repository=repo, schema=registry)

        req = CheckRequest.from_strings("document:doc1", "can_view", "user:alice")
        assert engine.check(req).allowed is True

        req = CheckRequest.from_strings("document:doc1", "can_view", "user:bob")
        assert engine.check(req).allowed is False

    def test_unknown_namespace_and_relation_errors(self) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "viewer",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(),
        )
        registry.register(ns)

        repo = InMemoryRelationRepository()
        engine = CheckEngine(
            relations_repository=repo,
            schema=registry,
            enable_debug=True,
        )

        # Unknown relation in known namespace
        res = engine.check(
            CheckRequest.from_strings("document:doc1", "missing", "user:alice")
        )
        assert res.allowed is False
        assert res.debug_trace is not None
        assert any("Unknown relation or permission" in line for line in res.debug_trace)

        # Unknown namespace
        res = engine.check(
            CheckRequest.from_strings("nope:doc1", "viewer", "user:alice")
        )
        assert res.allowed is False
        assert res.debug_trace is not None
        assert any("Unknown namespace" in line for line in res.debug_trace)

    def test_permission_missing_rewrite(self, monkeypatch) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(),
            permissions=(),
        )
        registry.register(ns)

        def _fake_get_def(self, object_type: str, relation: str) -> dict:  # type: ignore[override]
            return {
                "type": SchemaDefinitionType.PERMISSION,
                "name": relation,
                "rewrite": None,
            }

        # Patch on the class, not the instance (slots prevents instance patching)
        monkeypatch.setattr(
            SchemaRegistry,
            "get_relation_definition",
            _fake_get_def,
            raising=False,
        )

        engine = CheckEngine(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
            enable_debug=True,
        )

        res = engine.check(
            CheckRequest.from_strings("document:doc1", "can_x", "user:alice")
        )
        assert res.allowed is False
        assert res.debug_trace is not None
        assert any("Permission has no rewrite" in line for line in res.debug_trace)

    def test_unknown_definition_type(self, monkeypatch) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(name="document", relations=(), permissions=())
        registry.register(ns)

        def _fake_get_def(self, object_type: str, relation: str) -> dict:  # type: ignore[override]
            return {"type": "weird", "name": relation}

        # Patch on the class, not the instance (slots prevents instance patching)
        monkeypatch.setattr(
            SchemaRegistry,
            "get_relation_definition",
            _fake_get_def,
            raising=False,
        )

        engine = CheckEngine(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
            enable_debug=True,
        )

        res = engine.check(
            CheckRequest.from_strings("document:doc1", "perm", "user:alice")
        )
        assert res.allowed is False
        assert res.debug_trace is not None
        assert any("Unknown definition type" in line for line in res.debug_trace)

    def test_depth_limit_reached(self) -> None:
        # Build a chain of relations longer than max_depth
        N = 6
        relation_list: list[RelationDef] = []
        for i in range(N):
            name = f"r{i}"
            next_name = f"r{i + 1}"
            relation_list.append(
                RelationDef.with_subjects(
                    name,
                    (SubjectReference.from_dict({"namespace": "user"}),),
                    rewrite=ComputedUsersetRule(next_name),
                )
            )
        relation_list.append(
            RelationDef.with_subjects(
                "r6",
                (SubjectReference.from_dict({"namespace": "user"}),),
            )
        )

        registry = SchemaRegistry()
        registry.register(
            NamespaceDef(
                name="document", relations=tuple(relation_list), permissions=()
            )
        )

        engine = CheckEngine(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
            max_depth=3,
            enable_debug=True,
        )

        res = engine.check(
            CheckRequest.from_strings("document:doc1", "r0", "user:alice")
        )
        assert res.allowed is False
        assert res.debug_trace is not None
        assert any("Max depth reached" in line for line in res.debug_trace)

    def test_cycle_detection_short_circuit(self) -> None:
        registry = SchemaRegistry()
        # Create a two-node cycle: a -> b -> a
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "a",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                    rewrite=ComputedUsersetRule("b"),
                ),
                RelationDef.with_subjects(
                    "b",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                    rewrite=ComputedUsersetRule("a"),
                ),
            ),
            permissions=(),
        )
        registry.register(ns)

        engine = CheckEngine(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
            enable_debug=True,
        )

        res = engine.check(CheckRequest.from_strings("document:doc", "a", "user:alice"))
        # No tuples -> should terminate and return False rather than infinite loop
        assert res.allowed is False
        assert res.debug_trace is not None
        # Should not examine any tuples because there are none
        assert res.tuples_examined == 0

    def test_compiled_cache_is_used_and_populated(self) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="doc",
            relations=(
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(
                PermissionDef(name="view", rewrite=ComputedUsersetRule("viewer")),
            ),
        )
        registry.register(ns)

        repo = InMemoryRelationRepository()

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
                for k in list(self._store.keys()):
                    if k[0] == namespace:
                        self._store.pop(k, None)

        cache = _FakeCache()
        engine = CheckEngine(
            relations_repository=repo,
            schema=registry,
            compiled_rules_cache=cache,
        )

        req = CheckRequest.from_strings("doc:1", "view", "user:alice")

        # First call: miss -> set stored with RewriteRule
        engine.check(req)
        assert cache.get_calls[0] == ("doc", "view")
        ns_name, rel_name, compiled = cache.set_calls[0]
        assert (ns_name, rel_name) == ("doc", "view")
        assert isinstance(compiled, RewriteRule)

        # Second call: hit -> no additional set
        before = len(cache.set_calls)
        engine.check(req)
        assert len(cache.set_calls) == before

    def test_compiled_cache_refreshes_after_schema_update(self) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
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
                PermissionDef(name="view", rewrite=ComputedUsersetRule("viewer")),
            ),
        )
        registry.register(ns)

        repo = InMemoryRelationRepository()
        repo.write(RelationTuple.from_string("doc:1#viewer@user:alice"))

        from zanzipy.storage.cache.abstract.rules import CompiledRuleCache

        class _FakeCache(CompiledRuleCache[RewriteRule]):
            def __init__(self) -> None:
                self.set_calls: list[tuple[str, str, RewriteRule]] = []
                self._store: dict[tuple[str, str], RewriteRule] = {}

            def get(self, namespace: str, name: str) -> RewriteRule | None:
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

        cache = _FakeCache()
        engine = CheckEngine(
            relations_repository=repo,
            schema=registry,
            compiled_rules_cache=cache,
        )
        req = CheckRequest.from_strings("doc:1", "view", "user:alice")

        assert engine.check(req).allowed is True

        registry.update_namespace(
            NamespaceDef(
                name="doc",
                relations=tuple(ns.relations.values()),
                permissions=(
                    PermissionDef(name="view", rewrite=ComputedUsersetRule("editor")),
                ),
            )
        )

        assert engine.check(req).allowed is False
        view_sets = [call for call in cache.set_calls if call[:2] == ("doc", "view")]
        assert len(view_sets) == 2
        _, _, refreshed = view_sets[-1]
        assert isinstance(refreshed, ComputedUsersetRule)
        assert refreshed.relation == "editor"
