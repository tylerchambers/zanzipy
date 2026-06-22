import pytest

from zanzipy.engine.expander import ExpansionEngine
from zanzipy.models.tuple import RelationTuple
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import (
    ComputedUsersetRule,
    DirectRule,
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


class TestExpansionEngine:
    def test_direct_and_subject_sets(self) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "owner",
                    (
                        SubjectReference.from_dict({"namespace": "user"}),
                        # allow group#member subject sets on owner for test purposes
                        SubjectReference.from_dict(
                            {"namespace": "group", "relation": "member"}
                        ),
                    ),
                ),
            ),
            permissions=(),
        )
        registry.register(ns)

        repo = InMemoryRelationRepository()
        repo.write(RelationTuple.from_string("document:doc#owner@user:alice"))
        repo.write(RelationTuple.from_string("document:doc#owner@user:bob"))
        repo.write(RelationTuple.from_string("document:doc#owner@group:eng#member"))

        engine = ExpansionEngine(
            relations_repository=repo,
            schema=registry,
        )
        sset = engine.expand("document", "doc", "owner")
        assert sset.users == {"user:alice", "user:bob"}
        assert sset.usersets == {"group:eng#member"}

    def test_union_intersection_exclusion(self) -> None:
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "owner", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
                RelationDef.with_subjects(
                    "editor", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
                RelationDef.with_subjects(
                    "banned", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(
                PermissionDef(
                    name="view_union",
                    rewrite=UnionRule(
                        children=(
                            ComputedUsersetRule("owner"),
                            ComputedUsersetRule("editor"),
                        )
                    ),
                ),
                PermissionDef(
                    name="edit_intersection",
                    rewrite=IntersectionRule(
                        children=(
                            ComputedUsersetRule("owner"),
                            ComputedUsersetRule("editor"),
                        )
                    ),
                ),
                PermissionDef(
                    name="comment_exclusion",
                    rewrite=ExclusionRule(
                        base=ComputedUsersetRule("viewer"),
                        subtract=ComputedUsersetRule("banned"),
                    ),
                ),
            ),
        )
        registry.register(ns)

        repo = InMemoryRelationRepository()
        repo.write(RelationTuple.from_string("document:d#owner@user:alice"))
        repo.write(RelationTuple.from_string("document:d#editor@user:carol"))
        repo.write(RelationTuple.from_string("document:d#viewer@user:bob"))
        repo.write(RelationTuple.from_string("document:d#banned@user:carol"))

        engine = ExpansionEngine(
            relations_repository=repo,
            schema=registry,
        )

        # union
        s_union = engine.expand("document", "d", "view_union")
        assert s_union.users == {"user:alice", "user:carol"}

        # intersection (should be empty)
        s_inter = engine.expand("document", "d", "edit_intersection")
        assert s_inter.users == set()

        # exclusion (bob viewer but carol banned)
        s_ex = engine.expand("document", "d", "comment_exclusion")
        assert s_ex.users == {"user:bob"}

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

        engine = ExpansionEngine(relations_repository=repo, schema=registry)

        owner_twice = engine.expand("document", "doc", "owner_twice")
        assert owner_twice.users == {"user:alice"}
        assert owner_twice.usersets == set()

        owner_minus_owner = engine.expand("document", "doc", "owner_minus_owner")
        assert owner_minus_owner.users == set()
        assert owner_minus_owner.usersets == set()

    def test_set_algebra_materializes_userset_anchors(self) -> None:
        registry = SchemaRegistry()
        group_ns = NamespaceDef(
            name="group",
            relations=(
                RelationDef.with_subjects(
                    "member",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(),
        )
        document_ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "owner",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
                RelationDef.with_subjects(
                    "viewer",
                    (
                        SubjectReference.from_dict(
                            {"namespace": "group", "relation": "member"}
                        ),
                    ),
                ),
                RelationDef.with_subjects(
                    "banned",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(
                PermissionDef(
                    name="owner_and_viewer",
                    rewrite=IntersectionRule(
                        children=(
                            ComputedUsersetRule("owner"),
                            ComputedUsersetRule("viewer"),
                        )
                    ),
                ),
                PermissionDef(
                    name="viewer_without_banned",
                    rewrite=ExclusionRule(
                        base=ComputedUsersetRule("viewer"),
                        subtract=ComputedUsersetRule("banned"),
                    ),
                ),
            ),
        )
        registry.register_many([group_ns, document_ns])

        repo = InMemoryRelationRepository()
        repo.write(RelationTuple.from_string("group:eng#member@user:alice"))
        repo.write(RelationTuple.from_string("group:eng#member@user:bob"))
        repo.write(RelationTuple.from_string("document:doc#owner@user:alice"))
        repo.write(RelationTuple.from_string("document:doc#viewer@group:eng#member"))
        repo.write(RelationTuple.from_string("document:doc#banned@user:alice"))

        engine = ExpansionEngine(relations_repository=repo, schema=registry)

        owner_and_viewer = engine.expand("document", "doc", "owner_and_viewer")
        assert owner_and_viewer.users == {"user:alice"}
        assert owner_and_viewer.usersets == set()

        viewer_without_banned = engine.expand(
            "document", "doc", "viewer_without_banned"
        )
        assert viewer_without_banned.users == {"user:bob"}
        assert viewer_without_banned.usersets == set()

    def test_tuple_to_userset_cross_namespace(self) -> None:
        registry = SchemaRegistry()
        folder_ns = NamespaceDef(
            name="folder",
            relations=(
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(),
        )
        document_ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "parent", (SubjectReference.from_dict({"namespace": "folder"}),)
                ),
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
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
        repo.write(RelationTuple.from_string("document:doc#parent@folder:f1"))
        repo.write(RelationTuple.from_string("folder:f1#viewer@user:alice"))

        engine = ExpansionEngine(
            relations_repository=repo,
            schema=registry,
        )
        sset = engine.expand("document", "doc", "can_view")
        assert sset.users == {"user:alice"}

    def test_exceptions_and_validation(self, monkeypatch) -> None:
        # Base registry with one simple relation
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "viewer", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(),
        )
        registry.register(ns)

        repo = InMemoryRelationRepository()
        engine = ExpansionEngine(relations_repository=repo, schema=registry)

        # Invalid namespace identifier -> IdentifierValidationError from NamespaceId
        from zanzipy.models.errors import IdentifierValidationError

        with pytest.raises(IdentifierValidationError):
            engine.expand("bad ns", "x", "viewer")

        # Unknown relation in known namespace -> ValueError from registry
        with pytest.raises(ValueError, match="Unknown relation or permission"):
            engine.expand("document", "doc", "missing")

        # Permission missing rewrite -> ValueError("Permission has no rewrite")
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

        with pytest.raises(ValueError, match="Permission has no rewrite"):
            engine.expand("document", "doc", "perm")

        # Unknown definition type -> ValueError("Unknown definition type")
        def _fake_get_def2(self, object_type: str, relation: str) -> dict:  # type: ignore[override]
            return {"type": "weird", "name": relation}

        monkeypatch.setattr(
            SchemaRegistry,
            "get_relation_definition",
            _fake_get_def2,
            raising=False,
        )

        with pytest.raises(ValueError, match="Unknown definition type"):
            engine.expand("document", "doc", "weird")

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

        # Fake cache that lets us assert get/set interactions
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
        engine = ExpansionEngine(
            relations_repository=repo,
            schema=registry,
            compiled_rules_cache=cache,  # inject
        )

        # First call: cache miss -> set occurs with a RewriteRule
        engine.expand("doc", "d1", "view")
        assert cache.get_calls[0] == ("doc", "view")
        ns_name, rel_name, compiled = cache.set_calls[0]
        assert (ns_name, rel_name) == ("doc", "view")
        assert isinstance(
            compiled,
            (
                DirectRule,
                ComputedUsersetRule,
                UnionRule,
                IntersectionRule,
                ExclusionRule,
                TupleToUsersetRule,
            ),
        )

        # Second call: should hit cache and not call set again
        before_sets = len(cache.set_calls)
        engine.expand("doc", "d2", "view")
        assert len(cache.set_calls) == before_sets

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
        engine = ExpansionEngine(
            relations_repository=repo,
            schema=registry,
            compiled_rules_cache=cache,
        )

        initial = engine.expand("doc", "1", "view")
        assert initial.users == {"user:alice"}

        registry.update_namespace(
            NamespaceDef(
                name="doc",
                relations=tuple(ns.relations.values()),
                permissions=(
                    PermissionDef(name="view", rewrite=ComputedUsersetRule("editor")),
                ),
            )
        )

        refreshed = engine.expand("doc", "1", "view")
        assert refreshed.users == set()
        view_sets = [call for call in cache.set_calls if call[:2] == ("doc", "view")]
        assert len(view_sets) == 2
        _, _, compiled = view_sets[-1]
        assert isinstance(compiled, ComputedUsersetRule)
        assert compiled.relation == "editor"


class TestSubjectSetOps:
    def test_union_merges_both_buckets(self) -> None:
        from zanzipy.engine.expander import SubjectSet

        a = SubjectSet(users={"user:alice"}, usersets={"group:eng#member"})
        b = SubjectSet(users={"user:bob"}, usersets={"group:design#member"})

        c = a.union(b)
        assert c.users == {"user:alice", "user:bob"}
        assert c.usersets == {"group:eng#member", "group:design#member"}

    def test_intersection_and_difference(self) -> None:
        from zanzipy.engine.expander import SubjectSet

        a = SubjectSet(users={"user:alice", "user:bob"}, usersets={"group:eng#member"})
        b = SubjectSet(users={"user:bob", "user:carol"}, usersets={"group:eng#member"})

        inter = a.intersection(b)
        assert inter.users == {"user:bob"}
        assert inter.usersets == {"group:eng#member"}

        diff = a.difference(b)
        assert diff.users == {"user:alice"}
        assert diff.usersets == set()

    def test_depth_limit_and_cycle(self) -> None:
        # Build a cyclic schema: x -> y -> x, verify expand returns empty quickly
        registry = SchemaRegistry()
        ns = NamespaceDef(
            name="doc",
            relations=(
                RelationDef.with_subjects(
                    "x",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                    rewrite=ComputedUsersetRule("y"),
                ),
                RelationDef.with_subjects(
                    "y",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                    rewrite=ComputedUsersetRule("x"),
                ),
            ),
            permissions=(),
        )
        registry.register(ns)

        engine = ExpansionEngine(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
            max_depth=3,
        )
        sset = engine.expand("doc", "d1", "x")
        assert sset.users == set()
        assert sset.usersets == set()
