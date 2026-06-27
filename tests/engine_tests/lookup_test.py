from typing import TYPE_CHECKING

from zanzipy.client import ZanzibarClient
from zanzipy.models import RelationTuple, TupleFilter
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
    from collections.abc import Iterable

    from zanzipy.storage.revision import ReadContext, Revision, RevisionToken


class RecordingInMemoryRelationRepository(InMemoryRelationRepository):
    def __init__(self) -> None:
        super().__init__()
        self.forward_filters: list[TupleFilter] = []
        self.reverse_filters: list[TupleFilter] = []

    def read(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        self.forward_filters.append(filter)
        return super().read(filter, context=context)

    def read_reverse(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        self.reverse_filters.append(filter)
        return super().read(filter, context=context)


def _user_ref() -> SubjectReference:
    return SubjectReference.from_dict({"namespace": "user"})


def _group_member_ref() -> SubjectReference:
    return SubjectReference.from_dict({"namespace": "group", "relation": "member"})


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


def _check_oracle_resources(
    client: ZanzibarClient,
    object_type: str,
    relation: str,
    subject: str,
    *,
    revision: Revision | RevisionToken | None = None,
) -> set[str]:
    """Compute slow LookupResources results by checking every candidate."""

    checked: set[str] = set()
    for object_ref in _candidate_resource_objects(
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


def _assert_lookup_matches_check_oracle(
    client: ZanzibarClient,
    object_type: str,
    relation: str,
    subject: str,
    *,
    revision: Revision | RevisionToken | None = None,
) -> set[str]:
    """Assert production lookup matches the slow check-every-candidate oracle."""

    expected = _check_oracle_resources(
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
    actual = set(lookup)
    assert len(lookup) == len(actual)
    assert actual == expected
    return actual


def test_lookup_matches_namespace_wildcard_subjects() -> None:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "viewer",
                    (
                        _user_ref(),
                        SubjectReference(namespace="user", wildcard=True),
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
            ("document:public", "viewer", "user:*"),
            ("document:private", "viewer", "user:bob"),
        ]
    )

    assert client.list_objects("document", "viewer", "user:alice") == [
        "document:public"
    ]
    assert client.list_objects("document", "viewer", "user:bob") == [
        "document:private",
        "document:public",
    ]
    assert client.list_objects("document", "viewer", "service:alice") == []


def test_lookup_subject_bucket_filters_exact_direct_subjects() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="user",
                relations=(RelationDef.with_subjects("delegate", (_user_ref(),)),),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
                        (
                            _user_ref(),
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
        ]
    )

    assert client.check("document:delegated", "viewer", "user:alice") is False
    assert client.list_objects("document", "viewer", "user:alice") == [
        "document:direct"
    ]


def test_lookup_walks_namespace_wildcard_userset_edges() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "member",
                        (
                            _user_ref(),
                            SubjectReference(namespace="user", wildcard=True),
                        ),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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
            ("group:eng", "member", "user:*"),
            ("document:spec", "viewer", "group:eng#member"),
        ]
    )

    assert client.check("document:spec", "viewer", "user:alice") is True
    assert client.list_objects("document", "viewer", "user:alice") == ["document:spec"]
    assert client.list_objects("document", "viewer", "service:alice") == []


def test_direct_relation_lookup_uses_reverse_reads_only() -> None:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects("owner", (_user_ref(),)),
                RelationDef.with_subjects("editor", (_user_ref(),)),
            ),
            permissions=(PermissionDef("can_view", ComputedUsersetRule("owner")),),
        )
    )
    repo = RecordingInMemoryRelationRepository()
    client = ZanzibarClient(relations_repository=repo, schema=registry)
    client.write_many(
        [
            ("document:owned", "owner", "user:alice"),
            ("document:edited", "editor", "user:alice"),
            ("document:bob", "owner", "user:bob"),
        ]
    )
    repo.forward_filters.clear()
    repo.reverse_filters.clear()

    assert client.list_objects("document", "can_view", "user:alice") == [
        "document:owned"
    ]
    assert repo.forward_filters == []
    assert TupleFilter(subject_type="user", subject_id="alice") in repo.reverse_filters
    assert TupleFilter(subject_type="user", subject_id="*") in repo.reverse_filters
    assert all(
        filter.object_type is None
        and filter.object_id is None
        and filter.relation is None
        for filter in repo.reverse_filters
    )


def test_lookup_walks_group_usersets_backwards() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(), _group_member_ref()),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
                        (_user_ref(), _group_member_ref()),
                    ),
                ),
                permissions=(PermissionDef("can_view", ComputedUsersetRule("viewer")),),
            ),
        ]
    )
    repo = RecordingInMemoryRelationRepository()
    client = ZanzibarClient(
        relations_repository=repo,
        schema=registry,
    )
    client.write_many(
        [
            ("group:platform", "member", "user:alice"),
            ("group:eng", "member", "group:platform#member"),
            ("document:handbook", "viewer", "group:eng#member"),
            ("document:private", "viewer", "group:hr#member"),
        ]
    )

    assert client.list_objects("document", "can_view", "user:alice") == [
        "document:handbook"
    ]
    assert repo.forward_filters == []
    assert all(
        filter.object_type is None
        and filter.object_id is None
        and filter.relation is None
        for filter in repo.reverse_filters
    )


def test_lookup_keeps_userset_relation_ids_distinct() -> None:
    group_admin_ref = SubjectReference.from_dict(
        {"namespace": "group", "relation": "admin"}
    )
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects("member", (_user_ref(),)),
                    RelationDef.with_subjects("admin", (_user_ref(),)),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
                        (_group_member_ref(), group_admin_ref),
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
            ("document:member-doc", "viewer", "group:eng#member"),
            ("document:admin-doc", "viewer", "group:eng#admin"),
        ]
    )

    assert client.check("document:admin-doc", "viewer", "user:alice") is False
    assert client.list_objects("document", "viewer", "user:alice") == [
        "document:member-doc"
    ]


def test_lookup_subject_set_cycles_converge_with_seen_nodes() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(), _group_member_ref()),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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
            ("group:a", "member", "group:b#member"),
            ("document:cycle", "viewer", "group:b#member"),
        ]
    )

    assert client.list_objects("document", "viewer", "user:alice") == ["document:cycle"]


def test_lookup_honors_computed_userset_rewrites_in_subject_sets() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects("admin", (_user_ref(),)),
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(),),
                        rewrite=UnionRule((ComputedUsersetRule("admin"),)),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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
            ("group:eng", "admin", "user:alice"),
            ("document:spec", "viewer", "group:eng#member"),
        ]
    )

    assert client.check("document:spec", "viewer", "user:alice") is True
    assert client.list_objects("document", "viewer", "user:alice") == ["document:spec"]


def test_lookup_honors_tuple_to_userset_rewrites_in_subject_sets() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="team",
                relations=(RelationDef.with_subjects("member", (_user_ref(),)),),
            ),
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "parent",
                        (SubjectReference.from_dict({"namespace": "team"}),),
                    ),
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(),),
                        rewrite=TupleToUsersetRule("parent", "member"),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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
            ("team:blue", "member", "user:alice"),
            ("group:eng", "parent", "team:blue"),
            ("document:spec", "viewer", "group:eng#member"),
        ]
    )

    assert client.check("document:spec", "viewer", "user:alice") is True
    assert client.list_objects("document", "viewer", "user:alice") == ["document:spec"]


def test_lookup_validates_non_direct_userset_rewrites_before_traversal() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(),),
                        rewrite=ExclusionRule(
                            ThisRule(), ComputedUsersetRule("banned")
                        ),
                    ),
                    RelationDef.with_subjects("banned", (_user_ref(),)),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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

    assert client.check("document:spec", "viewer", "user:alice") is False
    assert client.list_objects("document", "viewer", "user:alice") == []


def test_lookup_honors_intersection_rewrites_in_subject_sets() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects("candidate", (_user_ref(),)),
                    RelationDef.with_subjects("active", (_user_ref(),)),
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(),),
                        rewrite=IntersectionRule(
                            (
                                ComputedUsersetRule("candidate"),
                                ComputedUsersetRule("active"),
                            )
                        ),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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
            ("group:full", "candidate", "user:alice"),
            ("group:full", "active", "user:alice"),
            ("group:partial", "candidate", "user:alice"),
            ("document:full", "viewer", "group:full#member"),
            ("document:partial", "viewer", "group:partial#member"),
        ]
    )

    assert client.check("document:partial", "viewer", "user:alice") is False
    assert client.list_objects("document", "viewer", "user:alice") == ["document:full"]


def test_lookup_walks_nested_tuple_to_userset_edges_backwards() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="folder",
                relations=(
                    RelationDef.with_subjects("viewer", (_user_ref(),)),
                    RelationDef.with_subjects(
                        "parent",
                        (SubjectReference.from_dict({"namespace": "folder"}),),
                    ),
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
                relations=(
                    RelationDef.with_subjects(
                        "parent",
                        (SubjectReference.from_dict({"namespace": "folder"}),),
                    ),
                ),
                permissions=(
                    PermissionDef("can_view", TupleToUsersetRule("parent", "can_view")),
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

    assert client.list_objects("document", "can_view", "user:alice") == [
        "document:child-doc",
        "document:grandchild-doc",
        "document:root-doc",
    ]


def test_union_intersection_and_exclusion_lookup_are_correct() -> None:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects("owner", (_user_ref(),)),
                RelationDef.with_subjects("editor", (_user_ref(),)),
                RelationDef.with_subjects("banned", (_user_ref(),)),
            ),
            permissions=(
                PermissionDef(
                    "can_view",
                    UnionRule(
                        (ComputedUsersetRule("owner"), ComputedUsersetRule("editor"))
                    ),
                ),
                PermissionDef(
                    "can_comment",
                    IntersectionRule(
                        (ComputedUsersetRule("owner"), ComputedUsersetRule("editor"))
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
            ("document:bob", "owner", "user:bob"),
        ]
    )

    assert client.list_objects("document", "owner", "user:alice") == [
        "document:both",
        "document:owned",
    ]
    assert client.list_objects("document", "can_view", "user:alice") == [
        "document:both",
        "document:edited",
        "document:owned",
    ]
    assert client.list_objects("document", "can_comment", "user:alice") == [
        "document:both"
    ]
    assert client.list_objects("document", "can_download", "user:alice") == [
        "document:both",
        "document:owned",
    ]


def test_lookup_cycle_matches_check_for_path_local_exclusion_rewrite() -> None:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "viewer",
                    (_user_ref(),),
                    rewrite=ExclusionRule(
                        ThisRule(),
                        UnionRule(
                            (
                                ComputedUsersetRule("viewer"),
                                ComputedUsersetRule("banned"),
                            )
                        ),
                    ),
                ),
                RelationDef.with_subjects("banned", (_user_ref(),)),
            ),
        )
    )
    client = ZanzibarClient(
        relations_repository=InMemoryRelationRepository(),
        schema=registry,
    )
    client.write_many(
        [
            ("document:allowed", "viewer", "user:alice"),
            ("document:blocked", "viewer", "user:alice"),
            ("document:blocked", "banned", "user:alice"),
            ("document:banned-only", "banned", "user:alice"),
        ]
    )

    checked = [
        f"document:{object_id}"
        for object_id in ("allowed", "blocked", "banned-only")
        if client.check(f"document:{object_id}", "viewer", "user:alice")
    ]

    assert checked == ["document:allowed"]
    assert client.list_objects("document", "viewer", "user:alice") == checked


def test_lookup_filters_complex_userset_candidates_with_check_depth() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects("admin", (_user_ref(),)),
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(), _group_member_ref()),
                        rewrite=UnionRule((ThisRule(), ComputedUsersetRule("admin"))),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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

    assert client.check("document:spec", "viewer", "user:alice") is False
    assert client.list_objects("document", "viewer", "user:alice") == []


def test_lookup_direct_this_leaf_is_not_pruned_by_expression_depth() -> None:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "viewer",
                    (_user_ref(),),
                    rewrite=UnionRule((ThisRule(),)),
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

    assert client.check("document:spec", "viewer", "user:alice") is True
    assert client.list_objects("document", "viewer", "user:alice") == ["document:spec"]


def test_lookup_userset_subqueries_track_their_own_rewrite_cycles() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(),),
                        rewrite=ExclusionRule(
                            ThisRule(),
                            ComputedUsersetRule("member"),
                        ),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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
            ("document:doc", "viewer", "group:eng#member"),
        ]
    )

    assert client.check("document:doc", "viewer", "user:alice") is True
    assert client.list_objects("document", "viewer", "user:alice") == ["document:doc"]


def test_tuple_to_userset_lookup_uses_parent_reverse_reads() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="folder",
                relations=(RelationDef.with_subjects("viewer", (_user_ref(),)),),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "parent",
                        (SubjectReference.from_dict({"namespace": "folder"}),),
                    ),
                ),
                permissions=(
                    PermissionDef("can_view", TupleToUsersetRule("parent", "viewer")),
                ),
            ),
        ]
    )
    repo = RecordingInMemoryRelationRepository()
    client = ZanzibarClient(relations_repository=repo, schema=registry)
    client.write_many(
        [
            ("folder:shared", "viewer", "user:alice"),
            ("document:doc", "parent", "folder:shared"),
            ("document:other", "parent", "folder:private"),
        ]
    )
    repo.forward_filters.clear()
    repo.reverse_filters.clear()

    assert client.list_objects("document", "can_view", "user:alice") == ["document:doc"]
    assert repo.forward_filters == []
    assert (
        TupleFilter(
            object_type="document",
            relation="parent",
            subject_type="folder",
            subject_id="shared",
            subject_relation=TupleFilter.DIRECT_SUBJECT_RELATION,
        )
        in repo.reverse_filters
    )


def test_lookup_matches_check_oracle_for_direct_wildcards_and_subject_buckets() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="user",
                relations=(RelationDef.with_subjects("delegate", (_user_ref(),)),),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
                        (
                            _user_ref(),
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

    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "viewer",
        "user:alice",
    ) == {"document:direct", "document:public"}
    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "viewer",
        "user:bob",
    ) == {"document:bob", "document:public"}


def test_lookup_matches_check_oracle_for_nested_and_cyclic_subject_sets() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(), _group_member_ref()),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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

    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "viewer",
        "user:alice",
    ) == {"document:cycle", "document:nested"}


def test_lookup_matches_check_oracle_for_tuple_to_userset_shapes() -> None:
    folder_ref = SubjectReference.from_dict({"namespace": "folder"})
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="folder",
                relations=(
                    RelationDef.with_subjects("viewer", (_user_ref(),)),
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
                    PermissionDef("can_view", TupleToUsersetRule("parent", "can_view")),
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

    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "viewer_from_parent",
        "user:alice",
    ) == {"document:root-doc"}
    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "can_view",
        "user:alice",
    ) == {
        "document:child-doc",
        "document:grandchild-doc",
        "document:root-doc",
    }


def test_lookup_matches_check_oracle_for_boolean_rewrites() -> None:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects("owner", (_user_ref(),)),
                RelationDef.with_subjects("editor", (_user_ref(),)),
                RelationDef.with_subjects("banned", (_user_ref(),)),
            ),
            permissions=(
                PermissionDef(
                    "can_view",
                    UnionRule(
                        (ComputedUsersetRule("owner"), ComputedUsersetRule("editor"))
                    ),
                ),
                PermissionDef(
                    "can_comment",
                    IntersectionRule(
                        (ComputedUsersetRule("owner"), ComputedUsersetRule("editor"))
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

    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "can_view",
        "user:alice",
    ) == {"document:both", "document:edited", "document:owned"}
    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "can_comment",
        "user:alice",
    ) == {"document:both"}
    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "can_download",
        "user:alice",
    ) == {"document:both", "document:owned"}


def test_lookup_matches_check_oracle_across_tenants() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(), _group_member_ref()),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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

    assert _assert_lookup_matches_check_oracle(
        alpha,
        "document",
        "viewer",
        "user:alice",
    ) == {"document:alpha"}
    assert (
        _assert_lookup_matches_check_oracle(
            beta,
            "document",
            "viewer",
            "user:alice",
        )
        == set()
    )
    assert _assert_lookup_matches_check_oracle(
        beta,
        "document",
        "viewer",
        "user:bob",
    ) == {"document:beta"}


def test_lookup_matches_check_oracle_at_exact_revisions() -> None:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="document",
            relations=(RelationDef.with_subjects("viewer", (_user_ref(),)),),
        )
    )
    client = ZanzibarClient(
        relations_repository=InMemoryRelationRepository(),
        schema=registry,
    )
    first = client.write("document:first", "viewer", "user:alice")
    second = client.write("document:second", "viewer", "user:alice")
    client.delete("document:first", "viewer", "user:alice")

    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "viewer",
        "user:alice",
        revision=first.token,
    ) == {"document:first"}
    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "viewer",
        "user:alice",
        revision=second.token,
    ) == {"document:first", "document:second"}
    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "viewer",
        "user:alice",
    ) == {"document:second"}


def test_lookup_matches_check_oracle_for_max_depth_cutoffs() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects("admin", (_user_ref(),)),
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(), _group_member_ref()),
                        rewrite=UnionRule((ThisRule(), ComputedUsersetRule("admin"))),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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
        _assert_lookup_matches_check_oracle(
            client,
            "document",
            "viewer",
            "user:alice",
        )
        == set()
    )


def test_lookup_matches_check_oracle_for_non_direct_userset_validation() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "member",
                        (_user_ref(),),
                        rewrite=ExclusionRule(
                            ThisRule(),
                            ComputedUsersetRule("banned"),
                        ),
                    ),
                    RelationDef.with_subjects("banned", (_user_ref(),)),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects("viewer", (_group_member_ref(),)),
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
        _assert_lookup_matches_check_oracle(
            client,
            "document",
            "viewer",
            "user:alice",
        )
        == set()
    )


def test_lookup_matches_check_oracle_for_low_depth_direct_this_leaves() -> None:
    registry = SchemaRegistry()
    registry.register(
        NamespaceDef(
            name="document",
            relations=(
                RelationDef.with_subjects(
                    "viewer",
                    (_user_ref(),),
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

    assert _assert_lookup_matches_check_oracle(
        client,
        "document",
        "viewer",
        "user:alice",
    ) == {"document:spec"}
