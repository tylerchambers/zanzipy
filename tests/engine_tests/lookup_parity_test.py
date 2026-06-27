from typing import TYPE_CHECKING

from zanzipy.client import ZanzibarClient
from zanzipy.models import TupleFilter
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
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository

if TYPE_CHECKING:
    from zanzipy.storage.revision import Revision, RevisionToken


class TestLookupResourcesParityOracle:
    """LookupResources parity tests against the slow check-every-candidate oracle."""

    @staticmethod
    def _user_ref() -> SubjectReference:
        return SubjectReference.from_dict({"namespace": "user"})

    @staticmethod
    def _group_member_ref() -> SubjectReference:
        return SubjectReference.from_dict({"namespace": "group", "relation": "member"})

    @staticmethod
    def _candidate_resource_objects(
        client: ZanzibarClient,
        object_type: str,
        *,
        revision: Revision | RevisionToken | None = None,
    ) -> set[str]:
        """Return candidate resources for the test-only slow lookup oracle.

        This intentionally scans every visible tuple in the resource namespace, then
        lets Check decide authorization. Production LookupResources must not use
        this candidate-scan shape.
        """

        tuple_filter = TupleFilter(object_type=object_type)
        if revision is None:
            relation_tuples = client.read_tuples(tuple_filter)
        else:
            relation_tuples = client.read_tuples_at_revision(
                tuple_filter,
                revision=revision,
            )
        return {str(relation_tuple.object) for relation_tuple in relation_tuples}

    @classmethod
    def _check_oracle_resources(
        cls,
        client: ZanzibarClient,
        object_type: str,
        relation: str,
        subject: str,
        *,
        revision: Revision | RevisionToken | None = None,
    ) -> set[str]:
        """Compute slow LookupResources results by checking every candidate."""

        checked: set[str] = set()
        for object_ref in cls._candidate_resource_objects(
            client,
            object_type,
            revision=revision,
        ):
            if revision is None:
                allowed = client.check(object_ref, relation, subject)
            else:
                allowed = client.check_at_revision(
                    object_ref,
                    relation,
                    subject,
                    revision=revision,
                )
            if allowed:
                checked.add(object_ref)
        return checked

    @classmethod
    def _assert_lookup_matches_check_oracle(
        cls,
        client: ZanzibarClient,
        object_type: str,
        relation: str,
        subject: str,
        *,
        revision: Revision | RevisionToken | None = None,
    ) -> set[str]:
        """Assert production lookup matches the slow check-every-candidate oracle."""

        expected = cls._check_oracle_resources(
            client,
            object_type,
            relation,
            subject,
            revision=revision,
        )
        if revision is None:
            lookup = client.list_objects(object_type, relation, subject)
        else:
            lookup = client.list_objects_at_revision(
                object_type,
                relation,
                subject,
                revision=revision,
            )
        assert lookup == sorted(lookup)
        actual = set(lookup)
        assert len(lookup) == len(actual)
        assert actual == expected
        return actual

    def test_lookup_matches_check_oracle_for_direct_wildcards_and_subject_buckets(
        self,
    ) -> None:
        registry = SchemaRegistry()
        registry.register_many(
            [
                NamespaceDef(
                    name="user",
                    relations=(
                        RelationDef.with_subjects("delegate", (self._user_ref(),)),
                    ),
                ),
                NamespaceDef(
                    name="document",
                    relations=(
                        RelationDef.with_subjects(
                            "viewer",
                            (
                                self._user_ref(),
                                SubjectReference(namespace="user", wildcard=True),
                                SubjectReference.from_dict(
                                    {"namespace": "user", "relation": "delegate"}
                                ),
                            ),
                        ),
                    ),
                ),
            ]
        )
        client = ZanzibarClient(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
        )
        client.write_many(
            [
                ("document:direct", "viewer", "user:alice"),
                ("document:delegated", "viewer", "user:alice#delegate"),
                ("document:public", "viewer", "user:*"),
                ("document:bob", "viewer", "user:bob"),
            ]
        )

        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "viewer",
            "user:alice",
        ) == {"document:direct", "document:public"}
        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "viewer",
            "user:bob",
        ) == {"document:bob", "document:public"}

    def test_lookup_matches_check_oracle_for_nested_and_cyclic_subject_sets(
        self,
    ) -> None:
        registry = SchemaRegistry()
        registry.register_many(
            [
                NamespaceDef(
                    name="group",
                    relations=(
                        RelationDef.with_subjects(
                            "member",
                            (self._user_ref(), self._group_member_ref()),
                        ),
                    ),
                ),
                NamespaceDef(
                    name="document",
                    relations=(
                        RelationDef.with_subjects(
                            "viewer", (self._group_member_ref(),)
                        ),
                    ),
                ),
            ]
        )
        client = ZanzibarClient(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
            max_check_depth=25,
        )
        client.write_many(
            [
                ("group:a", "member", "user:alice"),
                ("group:b", "member", "group:a#member"),
                ("group:c", "member", "group:b#member"),
                ("group:cycle-a", "member", "user:alice"),
                ("group:cycle-a", "member", "group:cycle-b#member"),
                ("group:cycle-b", "member", "group:cycle-a#member"),
                ("document:nested", "viewer", "group:c#member"),
                ("document:cycle", "viewer", "group:cycle-b#member"),
                ("document:other", "viewer", "group:other#member"),
            ]
        )

        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "viewer",
            "user:alice",
        ) == {"document:cycle", "document:nested"}

    def test_lookup_matches_check_oracle_for_tuple_to_userset_shapes(self) -> None:
        folder_ref = SubjectReference.from_dict({"namespace": "folder"})
        registry = SchemaRegistry()
        registry.register_many(
            [
                NamespaceDef(
                    name="folder",
                    relations=(
                        RelationDef.with_subjects("viewer", (self._user_ref(),)),
                        RelationDef.with_subjects("parent", (folder_ref,)),
                    ),
                    permissions=(
                        PermissionDef(
                            "can_view",
                            UnionRule(
                                (
                                    ComputedUsersetRule("viewer"),
                                    TupleToUsersetRule("parent", "can_view"),
                                )
                            ),
                        ),
                    ),
                ),
                NamespaceDef(
                    name="document",
                    relations=(RelationDef.with_subjects("parent", (folder_ref,)),),
                    permissions=(
                        PermissionDef(
                            "viewer_from_parent", TupleToUsersetRule("parent", "viewer")
                        ),
                        PermissionDef(
                            "can_view", TupleToUsersetRule("parent", "can_view")
                        ),
                    ),
                ),
            ]
        )
        client = ZanzibarClient(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
        )
        client.write_many(
            [
                ("folder:root", "viewer", "user:alice"),
                ("folder:child", "parent", "folder:root"),
                ("folder:grandchild", "parent", "folder:child"),
                ("document:root-doc", "parent", "folder:root"),
                ("document:child-doc", "parent", "folder:child"),
                ("document:grandchild-doc", "parent", "folder:grandchild"),
                ("document:other-doc", "parent", "folder:other"),
            ]
        )

        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "viewer_from_parent",
            "user:alice",
        ) == {"document:root-doc"}
        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "can_view",
            "user:alice",
        ) == {
            "document:child-doc",
            "document:grandchild-doc",
            "document:root-doc",
        }

    def test_lookup_matches_check_oracle_for_boolean_rewrites(self) -> None:
        registry = SchemaRegistry()
        registry.register(
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("owner", (self._user_ref(),)),
                    RelationDef.with_subjects("editor", (self._user_ref(),)),
                    RelationDef.with_subjects("banned", (self._user_ref(),)),
                ),
                permissions=(
                    PermissionDef(
                        "can_view",
                        UnionRule(
                            (
                                ComputedUsersetRule("owner"),
                                ComputedUsersetRule("editor"),
                            )
                        ),
                    ),
                    PermissionDef(
                        "can_comment",
                        IntersectionRule(
                            (
                                ComputedUsersetRule("owner"),
                                ComputedUsersetRule("editor"),
                            )
                        ),
                    ),
                    PermissionDef(
                        "can_download",
                        ExclusionRule(
                            UnionRule(
                                (
                                    ComputedUsersetRule("owner"),
                                    ComputedUsersetRule("editor"),
                                )
                            ),
                            ComputedUsersetRule("banned"),
                        ),
                    ),
                ),
            )
        )
        client = ZanzibarClient(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
        )
        client.write_many(
            [
                ("document:owned", "owner", "user:alice"),
                ("document:edited", "editor", "user:alice"),
                ("document:both", "owner", "user:alice"),
                ("document:both", "editor", "user:alice"),
                ("document:edited", "banned", "user:alice"),
                ("document:banned-only", "banned", "user:alice"),
                ("document:bob", "owner", "user:bob"),
            ]
        )

        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "can_view",
            "user:alice",
        ) == {"document:both", "document:edited", "document:owned"}
        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "can_comment",
            "user:alice",
        ) == {"document:both"}
        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "can_download",
            "user:alice",
        ) == {"document:both", "document:owned"}

    def test_lookup_matches_check_oracle_across_tenants(self) -> None:
        registry = SchemaRegistry()
        registry.register_many(
            [
                NamespaceDef(
                    name="group",
                    relations=(
                        RelationDef.with_subjects(
                            "member",
                            (self._user_ref(), self._group_member_ref()),
                        ),
                    ),
                ),
                NamespaceDef(
                    name="document",
                    relations=(
                        RelationDef.with_subjects(
                            "viewer", (self._group_member_ref(),)
                        ),
                    ),
                ),
            ]
        )
        repo = InMemoryRelationRepository()
        alpha = ZanzibarClient(
            relations_repository=repo,
            schema=registry,
            tenant="alpha",
        )
        beta = ZanzibarClient(
            relations_repository=repo,
            schema=registry,
            tenant="beta",
        )
        alpha.write_many(
            [
                ("group:eng", "member", "user:alice"),
                ("document:alpha", "viewer", "group:eng#member"),
            ]
        )
        beta.write_many(
            [
                ("group:eng", "member", "user:bob"),
                ("document:beta", "viewer", "group:eng#member"),
            ]
        )

        assert self._assert_lookup_matches_check_oracle(
            alpha,
            "document",
            "viewer",
            "user:alice",
        ) == {"document:alpha"}
        assert (
            self._assert_lookup_matches_check_oracle(
                beta,
                "document",
                "viewer",
                "user:alice",
            )
            == set()
        )
        assert self._assert_lookup_matches_check_oracle(
            beta,
            "document",
            "viewer",
            "user:bob",
        ) == {"document:beta"}

    def test_lookup_matches_check_oracle_at_exact_revisions(self) -> None:
        registry = SchemaRegistry()
        registry.register(
            NamespaceDef(
                name="document",
                relations=(RelationDef.with_subjects("viewer", (self._user_ref(),)),),
            )
        )
        client = ZanzibarClient(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
        )
        first = client.write("document:first", "viewer", "user:alice")
        second = client.write("document:second", "viewer", "user:alice")
        client.delete("document:first", "viewer", "user:alice")

        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "viewer",
            "user:alice",
            revision=first.token,
        ) == {"document:first"}
        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "viewer",
            "user:alice",
            revision=second.token,
        ) == {"document:first", "document:second"}
        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "viewer",
            "user:alice",
        ) == {"document:second"}

    def test_lookup_matches_check_oracle_for_max_depth_cutoffs(self) -> None:
        registry = SchemaRegistry()
        registry.register_many(
            [
                NamespaceDef(
                    name="group",
                    relations=(
                        RelationDef.with_subjects("admin", (self._user_ref(),)),
                        RelationDef.with_subjects(
                            "member",
                            (self._user_ref(), self._group_member_ref()),
                            rewrite=UnionRule(
                                (ThisRule(), ComputedUsersetRule("admin"))
                            ),
                        ),
                    ),
                ),
                NamespaceDef(
                    name="document",
                    relations=(
                        RelationDef.with_subjects(
                            "viewer", (self._group_member_ref(),)
                        ),
                    ),
                ),
            ]
        )
        client = ZanzibarClient(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
            max_check_depth=4,
        )
        client.write_many(
            [
                ("group:parent", "admin", "user:alice"),
                ("group:child", "member", "group:parent#member"),
                ("document:spec", "viewer", "group:child#member"),
            ]
        )

        assert (
            self._assert_lookup_matches_check_oracle(
                client,
                "document",
                "viewer",
                "user:alice",
            )
            == set()
        )

    def test_lookup_matches_check_oracle_for_non_direct_userset_validation(
        self,
    ) -> None:
        registry = SchemaRegistry()
        registry.register_many(
            [
                NamespaceDef(
                    name="group",
                    relations=(
                        RelationDef.with_subjects(
                            "member",
                            (self._user_ref(),),
                            rewrite=ExclusionRule(
                                ThisRule(),
                                ComputedUsersetRule("banned"),
                            ),
                        ),
                        RelationDef.with_subjects("banned", (self._user_ref(),)),
                    ),
                ),
                NamespaceDef(
                    name="document",
                    relations=(
                        RelationDef.with_subjects(
                            "viewer", (self._group_member_ref(),)
                        ),
                    ),
                ),
            ]
        )
        client = ZanzibarClient(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
        )
        client.write_many(
            [
                ("group:eng", "member", "user:alice"),
                ("group:eng", "banned", "user:alice"),
                ("document:spec", "viewer", "group:eng#member"),
            ]
        )

        assert (
            self._assert_lookup_matches_check_oracle(
                client,
                "document",
                "viewer",
                "user:alice",
            )
            == set()
        )

    def test_lookup_matches_check_oracle_for_low_depth_direct_this_leaves(self) -> None:
        registry = SchemaRegistry()
        registry.register(
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
                        (self._user_ref(),),
                        rewrite=UnionRule((IntersectionRule((ThisRule(),)),)),
                    ),
                ),
            )
        )
        client = ZanzibarClient(
            relations_repository=InMemoryRelationRepository(),
            schema=registry,
            max_check_depth=0,
        )
        client.write("document:spec", "viewer", "user:alice")

        assert self._assert_lookup_matches_check_oracle(
            client,
            "document",
            "viewer",
            "user:alice",
        ) == {"document:spec"}
