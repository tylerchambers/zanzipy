import pytest

from zanzipy.engine.checker import CheckEngine
from zanzipy.engine.expander import ExpansionEngine
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
        group = NamespaceDef(
            name="group",
            relations=(
                RelationDef.with_subjects(
                    "member", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(),
        )
        registry.register_many([ns, group])

        repo = InMemoryRelationRepository()
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#owner@user:alice")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#owner@user:bob")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#owner@group:eng#member")
                ),
            ),
        )

        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )
        sset = engine.expand("document", "doc", "owner", context=_context(repo))
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
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:d#owner@user:alice")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:d#editor@user:carol")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:d#viewer@user:bob")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:d#banned@user:carol")
                ),
            ),
        )

        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        # union
        s_union = engine.expand("document", "d", "view_union", context=_context(repo))
        assert s_union.users == {"user:alice", "user:carol"}

        # intersection (should be empty)
        s_inter = engine.expand(
            "document", "d", "edit_intersection", context=_context(repo)
        )
        assert s_inter.users == set()

        # exclusion (bob viewer but carol banned)
        s_ex = engine.expand(
            "document", "d", "comment_exclusion", context=_context(repo)
        )
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
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#owner@user:alice")
                ),
            ),
        )

        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        owner_twice = engine.expand(
            "document", "doc", "owner_twice", context=_context(repo)
        )
        assert owner_twice.users == {"user:alice"}
        assert owner_twice.usersets == set()

        owner_minus_owner = engine.expand(
            "document", "doc", "owner_minus_owner", context=_context(repo)
        )
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
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("group:eng#member@user:alice")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("group:eng#member@user:bob")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#owner@user:alice")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#viewer@group:eng#member")
                ),
            ),
        )
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#banned@user:alice")
                ),
            ),
        )

        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        owner_and_viewer = engine.expand(
            "document", "doc", "owner_and_viewer", context=_context(repo)
        )
        assert owner_and_viewer.users == {"user:alice"}
        assert owner_and_viewer.usersets == set()

        viewer_without_banned = engine.expand(
            "document", "doc", "viewer_without_banned", context=_context(repo)
        )
        assert viewer_without_banned.users == {"user:bob"}
        assert viewer_without_banned.usersets == set()

    def test_wildcard_intersection_materializes_finite_subject(self) -> None:
        registry = SchemaRegistry()
        document_ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "viewer",
                    (
                        SubjectReference.from_dict(
                            {"namespace": "user", "wildcard": True}
                        ),
                    ),
                ),
                RelationDef.with_subjects(
                    "editor",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
            permissions=(
                PermissionDef(
                    name="can_edit",
                    rewrite=IntersectionRule(
                        children=(
                            ComputedUsersetRule("viewer"),
                            ComputedUsersetRule("editor"),
                        )
                    ),
                ),
            ),
        )
        registry.register(document_ns)

        repo = InMemoryRelationRepository()
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:spec#viewer@user:*")
                ),
                TupleMutation.touch(
                    RelationTuple.from_string("document:spec#editor@user:alice")
                ),
            ),
        )
        model = _model(registry)
        context = _context(repo)
        checker = CheckEngine(relations_repository=repo, authorization_model=model)
        expander = ExpansionEngine(relations_repository=repo, authorization_model=model)

        assert (
            checker.check(
                CheckRequest.from_strings("document:spec", "can_edit", "user:alice"),
                context=context,
            ).allowed
            is True
        )
        assert (
            checker.check(
                CheckRequest.from_strings("document:spec", "can_edit", "user:bob"),
                context=context,
            ).allowed
            is False
        )

        sset = expander.expand("document", "spec", "can_edit", context=context)
        assert sset.users == {"user:alice"}
        assert sset.usersets == set()
        assert sset.wildcard_exclusions == {}

    def test_wildcard_exclusion_preserves_exceptions(self) -> None:
        registry = SchemaRegistry()
        document_ns = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "viewer",
                    (
                        SubjectReference.from_dict(
                            {"namespace": "user", "wildcard": True}
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
                    name="can_download",
                    rewrite=ExclusionRule(
                        base=ComputedUsersetRule("viewer"),
                        subtract=ComputedUsersetRule("banned"),
                    ),
                ),
            ),
        )
        registry.register(document_ns)

        repo = InMemoryRelationRepository()
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:spec#viewer@user:*")
                ),
                TupleMutation.touch(
                    RelationTuple.from_string("document:spec#banned@user:alice")
                ),
            ),
        )
        model = _model(registry)
        context = _context(repo)
        checker = CheckEngine(relations_repository=repo, authorization_model=model)
        expander = ExpansionEngine(relations_repository=repo, authorization_model=model)

        assert (
            checker.check(
                CheckRequest.from_strings(
                    "document:spec", "can_download", "user:alice"
                ),
                context=context,
            ).allowed
            is False
        )
        assert (
            checker.check(
                CheckRequest.from_strings("document:spec", "can_download", "user:bob"),
                context=context,
            ).allowed
            is True
        )

        sset = expander.expand("document", "spec", "can_download", context=context)
        assert sset.users == set()
        assert "user:*" not in sset.users
        assert sset.usersets == set()
        assert sset.wildcard_exclusions == {"user:*": {"user:alice"}}

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

        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
            max_depth=1,
        )
        sset = engine.expand("document", "doc", "viewer", context=_context(repo))
        assert sset.users == set()
        assert sset.usersets == set()

        complete_engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
            max_depth=25,
        )
        complete_sset = complete_engine.expand(
            "document", "doc", "viewer", context=_context(repo)
        )
        assert complete_sset.users == set()
        assert complete_sset.usersets == set()

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

        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )
        sset = engine.expand("document", "doc", "viewer", context=_context(repo))
        assert sset.users == set()
        assert sset.usersets == set()

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
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#parent@folder:f1")
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

        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )
        sset = engine.expand("document", "doc", "can_view", context=_context(repo))
        assert sset.users == {"user:alice"}

    def test_exceptions_and_validation(self) -> None:
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
        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        # Invalid namespace identifier -> IdentifierValidationError from NamespaceId
        from zanzipy.models.errors import IdentifierValidationError

        with pytest.raises(IdentifierValidationError):
            engine.expand("bad ns", "x", "viewer", context=_context(repo))

        # Unknown relation in known namespace -> ValueError from registry
        with pytest.raises(ValueError, match="Unknown relation or permission"):
            engine.expand("document", "doc", "missing", context=_context(repo))

    def test_direct_expansion_keeps_non_user_principals_in_usersets(self) -> None:
        registry = SchemaRegistry()
        registry.register(
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
                        (SubjectReference.from_dict({"namespace": "service"}),),
                    ),
                ),
            )
        )
        repo = InMemoryRelationRepository()
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#viewer@service:bot")
                ),
            ),
        )
        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        subjects = engine.expand("document", "doc", "viewer", context=_context(repo))

        assert subjects.users == set()
        assert subjects.usersets == {"service:bot"}

    def test_direct_expansion_skips_subjects_not_allowed_by_schema(self) -> None:
        registry = SchemaRegistry()
        registry.register(
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
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
                    RelationTuple.from_string("document:doc#viewer@service:bot")
                ),
            ),
        )
        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        subjects = engine.expand("document", "doc", "viewer", context=_context(repo))

        assert subjects.users == set()
        assert subjects.usersets == set()

    def test_tuple_to_userset_expansion_ignores_subject_set_edges(self) -> None:
        registry = SchemaRegistry()
        folder = NamespaceDef(
            name="folder",
            relations=(
                RelationDef.with_subjects(
                    "viewer",
                    (SubjectReference.from_dict({"namespace": "user"}),),
                ),
            ),
        )
        document = NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "parent",
                    (
                        SubjectReference.from_dict({"namespace": "folder"}),
                        SubjectReference.from_dict(
                            {"namespace": "folder", "relation": "viewer"}
                        ),
                    ),
                ),
            ),
            permissions=(
                PermissionDef("can_view", TupleToUsersetRule("parent", "viewer")),
            ),
        )
        registry.register_many([folder, document])
        repo = InMemoryRelationRepository()
        repo.write(
            WriteContext(DEFAULT_TENANT),
            (
                TupleMutation.touch(
                    RelationTuple.from_string("document:doc#parent@folder:f1#viewer")
                ),
                TupleMutation.touch(
                    RelationTuple.from_string("folder:f1#viewer@user:alice")
                ),
            ),
        )
        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
        )

        subjects = engine.expand("document", "doc", "can_view", context=_context(repo))

        assert subjects.users == set()
        assert subjects.usersets == set()


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

        repo = InMemoryRelationRepository()
        engine = ExpansionEngine(
            relations_repository=repo,
            authorization_model=_model(registry),
            max_depth=3,
        )
        sset = engine.expand("doc", "d1", "x", context=_context(repo))
        assert sset.users == set()
        assert sset.usersets == set()
