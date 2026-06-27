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

    from zanzipy.storage.revision import ReadContext


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
