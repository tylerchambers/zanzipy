import pytest

from zanzipy.models import (
    CheckRequest,
    CheckResponse,
    EntityId,
    LookupResourcesRequest,
    LookupResourcesResponse,
    NamespaceId,
    Obj,
    Relation,
    Subject,
    TupleFilter,
)


class TestCheckRequest:
    def test_from_parts_with_direct_subject(self) -> None:
        obj = Obj(NamespaceId("document"), EntityId("readme"))
        rel = Relation("owner")
        subj = Subject(NamespaceId("user"), EntityId("alice"))

        req = CheckRequest.from_parts(obj, rel, subj)

        assert req.object_type == "document"
        assert req.object_id == "readme"
        assert req.relation == "owner"
        assert req.subject_type == "user"
        assert req.subject_id == "alice"
        assert str(req) == "document:readme#owner@user:alice"

    def test_from_parts_rejects_userset_subject(self) -> None:
        obj = Obj(NamespaceId("folder"), EntityId("docs"))
        rel = Relation("viewer")
        subj = Subject(NamespaceId("group"), EntityId("eng"), Relation("member"))

        with pytest.raises(ValueError, match="direct subject"):
            CheckRequest.from_parts(obj, rel, subj)

    def test_from_strings(self) -> None:
        req = CheckRequest.from_strings(
            object_str="document:readme",
            relation="owner",
            subject_str="user:alice",
        )

        assert req.object_type == "document"
        assert req.object_id == "readme"
        assert req.relation == "owner"
        assert req.subject_type == "user"
        assert req.subject_id == "alice"

    def test_from_strings_rejects_userset_subject(self) -> None:
        with pytest.raises(ValueError, match="direct subject"):
            CheckRequest.from_strings(
                object_str="folder:docs",
                relation="viewer",
                subject_str="group:eng#member",
            )

    def test_to_filter_matches_fields(self) -> None:
        req = CheckRequest.from_strings(
            object_str="document:readme",
            relation="owner",
            subject_str="user:alice",
        )
        tf = req.to_filter()

        assert isinstance(tf, TupleFilter)
        assert tf.object_type == "document"
        assert tf.object_id == "readme"
        assert tf.relation == "owner"
        assert tf.subject_type == "user"
        assert tf.subject_id == "alice"
        assert tf.subject_relation == TupleFilter.DIRECT_SUBJECT_RELATION

    def test_converters_to_domain_objects(self) -> None:
        req = CheckRequest.from_strings(
            object_str="document:readme",
            relation="owner",
            subject_str="user:alice",
        )

        obj = req.to_object()
        rel = req.to_relation()
        subj = req.to_subject()

        assert isinstance(obj, Obj)
        assert str(obj.namespace) == "document"
        assert str(obj.id) == "readme"
        assert isinstance(rel, Relation)
        assert str(rel) == "owner"
        assert isinstance(subj, Subject)
        assert str(subj.namespace) == "user"
        assert str(subj.id) == "alice"
        # direct subject (no relation)
        assert subj.relation is None


class TestCheckResponse:
    def test_defaults(self) -> None:
        resp = CheckResponse(allowed=True)
        assert resp.allowed is True
        assert resp.debug_trace is None
        assert resp.depth_reached == 0
        assert resp.tuples_examined == 0

    def test_fields_override(self) -> None:
        resp = CheckResponse(
            allowed=False,
            debug_trace=["expand group:eng"],
            depth_reached=2,
            tuples_examined=3,
        )
        assert resp.allowed is False
        assert resp.debug_trace == ["expand group:eng"]
        assert resp.depth_reached == 2
        assert resp.tuples_examined == 3


class TestLookupResourcesRequest:
    def test_from_strings(self) -> None:
        req = LookupResourcesRequest.from_strings(
            resource_type="document",
            permission="can_view",
            subject="user:alice",
        )

        assert req.resource_type == "document"
        assert req.permission == "can_view"
        assert isinstance(req.subject, Subject)
        assert req.subject.relation is None
        assert isinstance(req.to_resource_namespace(), NamespaceId)
        assert isinstance(req.to_permission(), Relation)

    def test_from_strings_rejects_userset_subject(self) -> None:
        with pytest.raises(ValueError, match="direct subject"):
            LookupResourcesRequest.from_strings(
                resource_type="document",
                permission="viewer",
                subject="group:eng#member",
            )


class TestLookupResourcesResponse:
    def test_fields(self) -> None:
        resource = Obj.from_string("document:readme")
        resp = LookupResourcesResponse(
            resources=(resource,),
            debug_trace=("lookup document#viewer@user:alice",),
            depth_reached=2,
            tuples_examined=3,
        )

        assert resp.resources == (resource,)
        assert resp.debug_trace == ("lookup document#viewer@user:alice",)
        assert resp.depth_reached == 2
        assert resp.tuples_examined == 3
