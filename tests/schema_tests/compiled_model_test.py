import pytest

from zanzipy.schema.compiled import CompiledAuthorizationModel, CompiledSubjectReference
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import (
    ComputedUsersetRule,
    DirectRule,
    RewriteRule,
    TupleToUsersetRule,
)
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.cache.concrete.lru_rules import LruCompiledRuleCache


def _user_ref() -> SubjectReference:
    return SubjectReference(namespace="user")


def _userset_ref(namespace: str, relation: str) -> SubjectReference:
    return SubjectReference(namespace=namespace, relation=relation)


def _object_ref(namespace: str) -> SubjectReference:
    return SubjectReference(namespace=namespace)


def _member_namespace(name: str) -> NamespaceDef:
    return NamespaceDef(
        name=name,
        relations=(RelationDef.with_subjects("member", (_user_ref(),)),),
    )


def _viewable_namespace(name: str) -> NamespaceDef:
    return NamespaceDef(
        name=name,
        relations=(RelationDef.with_subjects("viewer", (_user_ref(),)),),
    )


def _document_namespace(
    *,
    view_relation: str = "viewer",
    userset_namespace: str = "group",
    parent_namespace: str = "folder",
) -> NamespaceDef:
    return NamespaceDef(
        name="document",
        relations=(
            RelationDef.with_subjects(
                "viewer",
                (
                    _user_ref(),
                    _userset_ref(userset_namespace, "member"),
                    SubjectReference(namespace="user", wildcard=True),
                ),
            ),
            RelationDef.with_subjects("editor", (_user_ref(),)),
            RelationDef.with_subjects("parent", (_object_ref(parent_namespace),)),
        ),
        permissions=(
            PermissionDef("can_view", ComputedUsersetRule(view_relation)),
            PermissionDef("inherited_view", TupleToUsersetRule("parent", "viewer")),
        ),
    )


def _registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register_many(
        (
            _member_namespace("group"),
            _member_namespace("team"),
            _viewable_namespace("folder"),
            _viewable_namespace("project"),
            _document_namespace(),
        )
    )
    return registry


class TestCompiledAuthorizationModel:
    def test_relation_without_rewrite_compiles_to_direct_rule(self) -> None:
        model = CompiledAuthorizationModel.from_schema(_registry())

        rewrite = model.resolve("document", "viewer")

        assert isinstance(rewrite, DirectRule)
        assert model.namespaces == ("document", "folder", "group", "project", "team")

    def test_relation_with_explicit_rewrite_does_not_compile_to_direct_rule(
        self,
    ) -> None:
        registry = SchemaRegistry()
        registry.register(
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_user_ref(),)),
                    RelationDef.with_subjects(
                        "delegated_viewer",
                        (_user_ref(),),
                        rewrite=ComputedUsersetRule("viewer"),
                    ),
                ),
            )
        )

        rewrite = CompiledAuthorizationModel.from_schema(registry).resolve(
            "document",
            "delegated_viewer",
        )

        assert isinstance(rewrite, ComputedUsersetRule)
        assert rewrite.relation == "viewer"

    def test_permission_resolves_to_typed_current_rewrite(self) -> None:
        model = CompiledAuthorizationModel.from_schema(_registry())

        rewrite = model.resolve("document", "can_view")

        assert isinstance(rewrite, ComputedUsersetRule)
        assert rewrite.relation == "viewer"

    def test_lookup_metadata_compiles_from_relation_subjects(self) -> None:
        model = CompiledAuthorizationModel.from_schema(_registry())

        assert model.allowed_userset_refs(
            resource_type="document",
            relation="viewer",
        ) == (("group", "member"),)
        assert model.tuple_to_userset_target_types(
            resource_type="document",
            tuple_relation="parent",
        ) == ("folder",)

    def test_schema_changes_require_model_rebuild_for_all_metadata(self) -> None:
        registry = _registry()
        old_model = CompiledAuthorizationModel.from_schema(registry)

        registry.update_namespace(
            _document_namespace(
                view_relation="editor",
                userset_namespace="team",
                parent_namespace="project",
            )
        )

        old_rewrite = old_model.resolve("document", "can_view")
        assert isinstance(old_rewrite, ComputedUsersetRule)
        assert old_rewrite.relation == "viewer"
        assert old_model.allowed_userset_refs(
            resource_type="document",
            relation="viewer",
        ) == (("group", "member"),)
        assert old_model.tuple_to_userset_target_types(
            resource_type="document",
            tuple_relation="parent",
        ) == ("folder",)

        rebuilt_model = CompiledAuthorizationModel.from_schema(registry)
        rebuilt_rewrite = rebuilt_model.resolve("document", "can_view")
        assert isinstance(rebuilt_rewrite, ComputedUsersetRule)
        assert rebuilt_rewrite.relation == "editor"
        assert rebuilt_model.allowed_userset_refs(
            resource_type="document",
            relation="viewer",
        ) == (("team", "member"),)
        assert rebuilt_model.tuple_to_userset_target_types(
            resource_type="document",
            tuple_relation="parent",
        ) == ("project",)

    def test_matching_cached_rule_is_reused_by_identity(self) -> None:
        cache = LruCompiledRuleCache[RewriteRule](max_entries=10, ttl_seconds=None)
        cached = ComputedUsersetRule("viewer")
        cache.set("document", "can_view", cached)

        model = CompiledAuthorizationModel.from_schema(
            _registry(),
            compiled_rules_cache=cache,
        )

        assert model.resolve("document", "can_view") is cached

    def test_stale_cached_rule_is_replaced_from_rebuilt_schema(self) -> None:
        cache = LruCompiledRuleCache[RewriteRule](max_entries=10, ttl_seconds=None)
        cache.set("document", "can_view", ComputedUsersetRule("viewer"))
        registry = _registry()
        registry.update_namespace(_document_namespace(view_relation="editor"))

        model = CompiledAuthorizationModel.from_schema(
            registry,
            compiled_rules_cache=cache,
        )

        rewrite = model.resolve("document", "can_view")
        assert isinstance(rewrite, ComputedUsersetRule)
        assert rewrite.relation == "editor"
        assert cache.get("document", "can_view") is rewrite

    def test_unknown_edges_raise_schema_compatible_errors(self) -> None:
        model = CompiledAuthorizationModel.from_schema(_registry())

        with pytest.raises(ValueError, match="Unknown namespace: missing"):
            model.resolve("missing", "viewer")
        with pytest.raises(
            ValueError,
            match="Unknown relation or permission 'missing' in namespace 'document'",
        ):
            model.resolve("document", "missing")
        with pytest.raises(
            ValueError,
            match="Unknown relation or permission 'missing' in namespace 'document'",
        ):
            model.allowed_userset_refs(resource_type="document", relation="missing")
        with pytest.raises(
            ValueError,
            match="Unknown relation or permission 'missing' in namespace 'document'",
        ):
            model.tuple_to_userset_target_types(
                resource_type="document",
                tuple_relation="missing",
            )

    def test_compiled_subject_reference_allows_direct_userset_and_wildcard(
        self,
    ) -> None:
        direct = CompiledSubjectReference(namespace="user")
        userset = CompiledSubjectReference(namespace="group", relation="member")
        wildcard = CompiledSubjectReference(namespace="user", wildcard=True)

        assert direct.allows(namespace="user", entity_id="alice", relation=None) is True
        assert direct.allows(namespace="user", entity_id="*", relation=None) is False
        assert (
            direct.allows(namespace="group", entity_id="alice", relation=None) is False
        )
        assert (
            userset.allows(
                namespace="group",
                entity_id="eng",
                relation="member",
            )
            is True
        )
        assert (
            userset.allows(
                namespace="group",
                entity_id="eng",
                relation="admin",
            )
            is False
        )
        assert wildcard.allows(namespace="user", entity_id="*", relation=None) is True
        assert (
            wildcard.allows(
                namespace="user",
                entity_id="alice",
                relation=None,
            )
            is False
        )

    def test_allows_tuple_to_userset_subject_rejects_wildcard_and_subject_set(
        self,
    ) -> None:
        model = CompiledAuthorizationModel.from_schema(_registry())

        assert (
            model.allows_tuple_to_userset_subject(
                resource_type="document",
                tuple_relation="parent",
                subject_type="folder",
                subject_id="docs",
                subject_relation=None,
            )
            is True
        )
        assert (
            model.allows_tuple_to_userset_subject(
                resource_type="document",
                tuple_relation="parent",
                subject_type="folder",
                subject_id="*",
                subject_relation=None,
            )
            is False
        )
        assert (
            model.allows_tuple_to_userset_subject(
                resource_type="document",
                tuple_relation="parent",
                subject_type="folder",
                subject_id="docs",
                subject_relation="viewer",
            )
            is False
        )
