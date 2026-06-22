import pytest

from zanzipy.client import ZanzibarClient
from zanzipy.dsl.builder import SchemaBuilder
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.storage.repos.concrete.memory.relations import InMemoryRelationRepository


def _client_from_builder() -> ZanzibarClient:
    registry = (
        SchemaBuilder(SchemaRegistry())
        .namespace("group")
        .relation("member", subjects="user")
        .done()
        .namespace("folder")
        .relation("viewer", subjects="user")
        .done()
        .namespace("document")
        .relation("parent", subjects="folder")
        .relation("viewer", subjects=["user", "group#member"])
        .relation("banned", subjects="user")
        .permission("can_view", union=["viewer", "parent->viewer"])
        .permission("can_comment", exclusion=("can_view", "banned"))
        .done()
        .build()
    )
    return ZanzibarClient(
        relations_repository=InMemoryRelationRepository(),
        schema=registry,
    )


class TestDslBuilderIntegration:
    def test_built_schema_drives_client_checks(self) -> None:
        client = _client_from_builder()

        client.write("group:eng", "member", "user:bob")
        client.write("document:doc1", "viewer", "group:eng#member")
        client.write("document:doc1", "viewer", "user:alice")
        client.write("document:doc1", "banned", "user:alice")

        client.write("folder:root", "viewer", "user:carol")
        client.write("document:doc2", "parent", "folder:root")

        assert client.check("document:doc1", "can_view", "user:bob") is True
        assert client.check("document:doc1", "can_comment", "user:bob") is True
        assert client.check("document:doc1", "can_comment", "user:alice") is False
        assert client.check("document:doc2", "can_view", "user:carol") is True
        assert client.check("document:doc2", "can_view", "user:bob") is False

    def test_built_schema_validates_tuple_writes(self) -> None:
        client = _client_from_builder()

        with pytest.raises(ValueError, match="Subject not allowed by schema"):
            client.write("document:doc1", "viewer", "folder:root")

        with pytest.raises(ValueError, match="Cannot write to permission"):
            client.write("document:doc1", "can_view", "user:alice")
