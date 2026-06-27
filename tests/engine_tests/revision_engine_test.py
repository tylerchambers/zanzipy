from zanzipy.engine.checker import CheckEngine
from zanzipy.models import CheckRequest, RelationTuple
from zanzipy.schema.compiled import CompiledAuthorizationModel
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import TupleToUsersetRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository
from zanzipy.storage.revision import ReadContext, TenantId, TupleMutation, WriteContext

DEFAULT_TENANT = TenantId("default")


def _model(registry: SchemaRegistry) -> CompiledAuthorizationModel:
    return CompiledAuthorizationModel.from_schema(registry)


def test_direct_check_respects_write_and_delete_revisions() -> None:
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
    engine = CheckEngine(
        relations_repository=repo,
        authorization_model=_model(registry),
    )
    tuple_ = RelationTuple.from_string("document:doc1#viewer@user:alice")
    before = repo.head_revision(DEFAULT_TENANT)
    write = repo.write(WriteContext(DEFAULT_TENANT), (TupleMutation.touch(tuple_),))
    delete = repo.write(WriteContext(DEFAULT_TENANT), (TupleMutation.delete(tuple_),))
    request = CheckRequest.from_strings("document:doc1", "viewer", "user:alice")

    assert (
        engine.check(request, context=ReadContext(DEFAULT_TENANT, before)).allowed
        is False
    )
    assert (
        engine.check(
            request, context=ReadContext(DEFAULT_TENANT, write.revision)
        ).allowed
        is True
    )
    assert (
        engine.check(
            request, context=ReadContext(DEFAULT_TENANT, delete.revision)
        ).allowed
        is False
    )


def test_group_membership_recursion_uses_same_revision() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="group",
                relations=(
                    RelationDef.with_subjects(
                        "member",
                        (SubjectReference.from_dict({"namespace": "user"}),),
                    ),
                ),
            ),
            NamespaceDef(
                name="document",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
                        (
                            SubjectReference.from_dict(
                                {"namespace": "group", "relation": "member"}
                            ),
                        ),
                    ),
                ),
            ),
        ]
    )
    repo = InMemoryRelationRepository()
    engine = CheckEngine(
        relations_repository=repo,
        authorization_model=_model(registry),
    )
    edge = RelationTuple.from_string("document:doc1#viewer@group:eng#member")
    member = RelationTuple.from_string("group:eng#member@user:alice")
    edge_write = repo.write(WriteContext(DEFAULT_TENANT), (TupleMutation.touch(edge),))
    member_write = repo.write(
        WriteContext(DEFAULT_TENANT), (TupleMutation.touch(member),)
    )
    member_delete = repo.write(
        WriteContext(DEFAULT_TENANT), (TupleMutation.delete(member),)
    )
    request = CheckRequest.from_strings("document:doc1", "viewer", "user:alice")

    assert (
        engine.check(
            request, context=ReadContext(DEFAULT_TENANT, edge_write.revision)
        ).allowed
        is False
    )
    assert (
        engine.check(
            request, context=ReadContext(DEFAULT_TENANT, member_write.revision)
        ).allowed
        is True
    )
    assert (
        engine.check(
            request,
            context=ReadContext(DEFAULT_TENANT, member_delete.revision),
        ).allowed
        is False
    )


def test_tuple_to_userset_recursion_uses_same_revision() -> None:
    registry = SchemaRegistry()
    registry.register_many(
        [
            NamespaceDef(
                name="folder",
                relations=(
                    RelationDef.with_subjects(
                        "viewer",
                        (SubjectReference.from_dict({"namespace": "user"}),),
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
                    PermissionDef(
                        name="can_view",
                        rewrite=TupleToUsersetRule(
                            tuple_relation="parent",
                            computed_relation="viewer",
                        ),
                    ),
                ),
            ),
        ]
    )
    repo = InMemoryRelationRepository()
    engine = CheckEngine(
        relations_repository=repo,
        authorization_model=_model(registry),
    )
    parent = RelationTuple.from_string("document:doc1#parent@folder:root")
    viewer = RelationTuple.from_string("folder:root#viewer@user:alice")
    parent_write = repo.write(
        WriteContext(DEFAULT_TENANT), (TupleMutation.touch(parent),)
    )
    viewer_write = repo.write(
        WriteContext(DEFAULT_TENANT), (TupleMutation.touch(viewer),)
    )
    viewer_delete = repo.write(
        WriteContext(DEFAULT_TENANT), (TupleMutation.delete(viewer),)
    )
    request = CheckRequest.from_strings("document:doc1", "can_view", "user:alice")

    assert (
        engine.check(
            request,
            context=ReadContext(DEFAULT_TENANT, parent_write.revision),
        ).allowed
        is False
    )
    assert (
        engine.check(
            request,
            context=ReadContext(DEFAULT_TENANT, viewer_write.revision),
        ).allowed
        is True
    )
    assert (
        engine.check(
            request,
            context=ReadContext(DEFAULT_TENANT, viewer_delete.revision),
        ).allowed
        is False
    )
