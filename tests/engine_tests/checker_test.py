from zanzipy.engine.checker import CheckEngine
from zanzipy.models.check import CheckRequest
from zanzipy.models.tuple import RelationTuple
from zanzipy.schema.compiled import CompiledAuthorizationModel
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import (
    ComputedUsersetRule,
    ExclusionRule,
    IntersectionRule,
    ThisRule,
    TupleToUsersetRule,
    UnionRule,
)
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.repos.concrete.memory.relations import (
    InMemoryRelationRepository,
)
from zanzipy.storage.revision import ReadContext, TenantId, TupleMutation, WriteContext

DEFAULT_TENANT = TenantId("default")


def _context(repo: InMemoryRelationRepository) -> ReadContext:
    return ReadContext(DEFAULT_TENANT, repo.head_revision(DEFAULT_TENANT))


def _model(registry: SchemaRegistry) -> CompiledAuthorizationModel:
    return CompiledAuthorizationModel.from_schema(registry)


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
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc1#owner@user:alice")
                ),
            ),
        )

        engine = CheckEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        # Positive
        req = CheckRequest.from_strings("document:doc1", "owner", "user:alice")
        res = engine.check(req, context=_context(repo))
        assert res.allowed is True

        # Negative
        req = CheckRequest.from_strings("document:doc1", "owner", "user:bob")
        res = engine.check(req, context=_context(repo))
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
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc1#owner@user:alice")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc2#editor@user:carol")
                ),
            ),
        )

        engine = CheckEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        # Viewer via owner
        req = CheckRequest.from_strings("document:doc1", "viewer", "user:alice")
        assert engine.check(req, context=_context(repo)).allowed is True

        # Viewer via editor
        req = CheckRequest.from_strings("document:doc2", "viewer", "user:carol")
        assert engine.check(req, context=_context(repo)).allowed is True

        # Permission can_view delegates to viewer
        req = CheckRequest.from_strings("document:doc1", "can_view", "user:alice")
        assert engine.check(req, context=_context(repo)).allowed is True

        # Negative
        req = CheckRequest.from_strings("document:doc2", "viewer", "user:alice")
        assert engine.check(req, context=_context(repo)).allowed is False

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
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc1#owner@user:alice")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc1#editor@user:alice")
                ),
            ),
        )
        # bob is only viewer of doc2 -> can_comment unless banned
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc2#viewer@user:bob")
                ),
            ),
        )
        # carol viewer and banned on doc3 -> cannot comment
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc3#viewer@user:carol")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc3#banned@user:carol")
                ),
            ),
        )

        engine = CheckEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        # Intersection
        req = CheckRequest.from_strings("document:doc1", "can_edit", "user:alice")
        assert engine.check(req, context=_context(repo)).allowed is True
        req = CheckRequest.from_strings("document:doc1", "can_edit", "user:bob")
        assert engine.check(req, context=_context(repo)).allowed is False

        # Exclusion
        req = CheckRequest.from_strings("document:doc2", "can_comment", "user:bob")
        assert engine.check(req, context=_context(repo)).allowed is True
        req = CheckRequest.from_strings("document:doc3", "can_comment", "user:carol")
        assert engine.check(req, context=_context(repo)).allowed is False

    def test_exclusion_subtract_depth_cutoff_fails_closed(self) -> None:
        registry = SchemaRegistry()
        registry.register(
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
                        (SubjectReference.from_dict({"namespace": "user"}),),
                        rewrite=ExclusionRule(
                            ThisRule(),
                            ComputedUsersetRule("blocked"),
                        ),
                    ),
                    RelationDef.with_subjects(
                        "blocked",
                        (SubjectReference.from_dict({"namespace": "user"}),),
                    ),
                ),
            )
        )

        repo = InMemoryRelationRepository()
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#viewer@user:alice")
                ),
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#blocked@user:alice")
                ),
            ),
        )

        engine = CheckEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
            max_depth=1,
            enable_debug=True,
        )

        res = engine.check(
            CheckRequest.from_strings("document:doc", "viewer", "user:alice"),
            context=_context(repo),
        )
        assert res.allowed is False
        assert res.debug_trace is not None
        assert any("Max depth reached" in line for line in res.debug_trace)

        complete_engine = CheckEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
            max_depth=25,
        )
        assert (
            complete_engine.check(
                CheckRequest.from_strings("document:doc", "viewer", "user:alice"),
                context=_context(repo),
            ).allowed
            is False
        )

    def test_exclusion_subtract_cycle_fails_closed(self) -> None:
        registry = SchemaRegistry()
        registry.register(
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
                        (SubjectReference.from_dict({"namespace": "user"}),),
                        rewrite=ExclusionRule(
                            ThisRule(),
                            ComputedUsersetRule("viewer"),
                        ),
                    ),
                ),
            )
        )

        repo = InMemoryRelationRepository()
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#viewer@user:alice")
                ),
            ),
        )

        engine = CheckEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
            enable_debug=True,
        )

        res = engine.check(
            CheckRequest.from_strings("document:doc", "viewer", "user:alice"),
            context=_context(repo),
        )
        assert res.allowed is False
        assert res.debug_trace is not None
        assert any("Cycle detected" in line for line in res.debug_trace)

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
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#owner@user:alice")
                ),
            ),
        )

        engine = CheckEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        req = CheckRequest.from_strings("document:doc", "owner_twice", "user:alice")
        assert engine.check(req, context=_context(repo)).allowed is True

        req = CheckRequest.from_strings(
            "document:doc", "owner_minus_owner", "user:alice"
        )
        assert engine.check(req, context=_context(repo)).allowed is False

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
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc1#parent@folder:f1")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("folder:f1#viewer@user:alice")
                ),
            ),
        )

        engine = CheckEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        req = CheckRequest.from_strings("document:doc1", "can_view", "user:alice")
        assert engine.check(req, context=_context(repo)).allowed is True

        req = CheckRequest.from_strings("document:doc1", "can_view", "user:bob")
        assert engine.check(req, context=_context(repo)).allowed is False

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
            authorization_model=_model(registry),
            enable_debug=True,
        )

        # Unknown relation in known namespace
        res = engine.check(
            CheckRequest.from_strings("document:doc1", "missing", "user:alice"),
            context=_context(repo),
        )
        assert res.allowed is False
        assert res.debug_trace is not None
        assert any("Unknown relation or permission" in line for line in res.debug_trace)

        # Unknown namespace
        res = engine.check(
            CheckRequest.from_strings("nope:doc1", "viewer", "user:alice"),
            context=_context(repo),
        )
        assert res.allowed is False
        assert res.debug_trace is not None
        assert any("Unknown namespace" in line for line in res.debug_trace)

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

        repo = InMemoryRelationRepository()
        engine = CheckEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
            max_depth=3,
            enable_debug=True,
        )

        res = engine.check(
            CheckRequest.from_strings("document:doc1", "r0", "user:alice"),
            context=_context(repo),
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

        repo = InMemoryRelationRepository()
        engine = CheckEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
            enable_debug=True,
        )

        res = engine.check(
            CheckRequest.from_strings("document:doc", "a", "user:alice"),
            context=_context(repo),
        )
        # No tuples -> should terminate and return False rather than infinite loop
        assert res.allowed is False
        assert res.debug_trace is not None
        # Should not examine any tuples because there are none
        assert res.tuples_examined == 0
