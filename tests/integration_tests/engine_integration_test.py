from contextvars import Context

import pytest

from zanzipy.engine_integration import ZanzibarEngine, get_authorization_engine
from zanzipy.models import Obj, Subject


class _Namespace:
    def __init__(self, name: str) -> None:
        self.name = name

    def to_dict(self) -> dict:
        return {"name": self.name, "permissions": {}, "relations": {}}


class _Schema:
    def __init__(self) -> None:
        self.requested_namespaces: list[str] = []

    def get_namespace(self, namespace: str) -> _Namespace:
        self.requested_namespaces.append(namespace)
        return _Namespace(namespace)


class _Client:
    def __init__(self) -> None:
        self.schema = _Schema()
        self.relations_repository = object()
        self.calls: list[tuple] = []

    def head_token(self) -> str:
        self.calls.append(("head_token",))
        return "token-1"

    def write(self, resource: str, relation: str, subject: str) -> str:
        self.calls.append(("write", resource, relation, subject))
        return "wrote"

    def delete(self, resource: str, relation: str, subject: str) -> str:
        self.calls.append(("delete", resource, relation, subject))
        return "deleted"

    def check(
        self,
        resource: str,
        permission: str,
        subject: str,
        *,
        consistency: object = None,
    ) -> bool:
        self.calls.append(("check", resource, permission, subject, consistency))
        return True

    def expand(
        self, resource: str, permission: str, *, consistency: object = None
    ) -> str:
        self.calls.append(("expand", resource, permission, consistency))
        return "expanded"

    def list_objects(
        self,
        resource_type: str,
        permission: str,
        subject: str,
        *,
        consistency: object = None,
    ) -> list[str]:
        self.calls.append(
            ("list_objects", resource_type, permission, subject, consistency)
        )
        return ["doc:1", "doc:2", "doc:3"]

    def read_tuples(
        self, tuple_filter: object, *, consistency: object = None
    ) -> tuple[str]:
        self.calls.append(("read_tuples", tuple_filter, consistency))
        return ("tuple",)

    def list_subjects_direct(self, resource: str, relation: str) -> list[str]:
        self.calls.append(("list_subjects_direct", resource, relation))
        return ["user:alice"]


class TestZanzibarEngineDelegates:
    def test_properties_and_client_methods_delegate_with_canonical_strings(
        self,
    ) -> None:
        client = _Client()
        engine = ZanzibarEngine(client)  # type: ignore[arg-type]
        resource = Obj.from_string("doc:1")
        subject = Subject.from_string("user:alice")
        consistency = object()

        assert engine.schema is client.schema
        assert engine.relations_repository is client.relations_repository
        assert engine.head_token() == "token-1"
        assert (
            engine.write_tuple(subject=subject, relation="owner", resource=resource)
            == "wrote"
        )
        assert (
            engine.delete_tuple(subject=subject, relation="owner", resource=resource)
            == "deleted"
        )
        assert (
            engine.check(
                subject=subject,
                permission="view",
                resource=resource,
                consistency=consistency,
            )
            is True
        )
        assert (
            engine.expand(permission="view", resource=resource, consistency=consistency)
            == "expanded"
        )
        assert engine.list_direct_subjects(resource=resource, relation="owner") == [
            "user:alice"
        ]

        assert client.calls == [
            ("head_token",),
            ("write", "doc:1", "owner", "user:alice"),
            ("delete", "doc:1", "owner", "user:alice"),
            ("check", "doc:1", "view", "user:alice", consistency),
            ("expand", "doc:1", "view", consistency),
            ("list_subjects_direct", "doc:1", "owner"),
        ]

    def test_list_resources_applies_optional_limit_after_client_lookup(self) -> None:
        client = _Client()
        engine = ZanzibarEngine(client)  # type: ignore[arg-type]
        subject = Subject.from_string("user:alice")
        consistency = object()

        assert [
            str(obj)
            for obj in engine.list_resources(
                subject=subject,
                permission="view",
                resource_type="doc",
                limit=2,
                consistency=consistency,
            )
        ] == ["doc:1", "doc:2"]
        assert client.calls == [
            ("list_objects", "doc", "view", "user:alice", consistency)
        ]

    def test_list_resources_keeps_all_objects_when_limit_is_none(self) -> None:
        client = _Client()
        engine = ZanzibarEngine(client)  # type: ignore[arg-type]

        assert [
            str(obj)
            for obj in engine.list_resources(
                subject=Subject.from_string("user:alice"),
                permission="view",
                resource_type="doc",
                limit=None,
            )
        ] == ["doc:1", "doc:2", "doc:3"]

    def test_read_tuples_requires_subject_or_resource(self) -> None:
        engine = ZanzibarEngine(_Client())  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="At least one of subject or resource"):
            engine.read_tuples()

    def test_read_tuples_builds_filter_from_subject_and_resource(self) -> None:
        client = _Client()
        engine = ZanzibarEngine(client)  # type: ignore[arg-type]
        subject = Subject.from_string("group:eng#member")
        resource = Obj.from_string("doc:1")
        consistency = object()

        assert engine.read_tuples(
            subject=subject, resource=resource, consistency=consistency
        ) == ("tuple",)
        _, tuple_filter, seen_consistency = client.calls[0]
        assert tuple_filter.object_ref == resource
        assert tuple_filter.subject_ref == subject
        assert seen_consistency is consistency

    def test_get_schema_returns_namespace_dict(self) -> None:
        client = _Client()
        engine = ZanzibarEngine(client)  # type: ignore[arg-type]

        assert engine.get_schema("doc") == {
            "name": "doc",
            "permissions": {},
            "relations": {},
        }
        assert client.schema.requested_namespaces == ["doc"]

    def test_get_authorization_engine_raises_when_context_unconfigured(self) -> None:
        empty_context = Context()

        with pytest.raises(RuntimeError, match="Authorization engine not configured"):
            empty_context.run(get_authorization_engine)
