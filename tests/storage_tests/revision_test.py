import pytest

from zanzipy.models import RelationTuple
from zanzipy.storage.revision import (
    AtExactRevision,
    AtLeastAsFresh,
    Consistency,
    FullyConsistent,
    ReadContext,
    RelationshipChange,
    RelationshipOperation,
    Revision,
    RevisionToken,
    TenantId,
    WriteContext,
    WriteResult,
    revision_for_consistency,
)


class TestRevisionValueObjects:
    def test_revision_rejects_invalid_values(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            Revision(-1)
        with pytest.raises(TypeError, match="int"):
            Revision(True)  # type: ignore[arg-type]

    def test_tenant_id_rejects_invalid_values(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            TenantId("")
        with pytest.raises(TypeError, match="str"):
            TenantId(123)  # type: ignore[arg-type]


class TestRevisionTokenAndContexts:
    def test_revision_token_requires_tenant_and_revision_values(self) -> None:
        tenant = TenantId("acme")
        revision = Revision(7)

        token = RevisionToken(tenant, revision)

        assert token.tenant == tenant
        assert token.revision == revision
        with pytest.raises(TypeError, match="TenantId"):
            RevisionToken("acme", revision)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="Revision"):
            RevisionToken(tenant, 7)  # type: ignore[arg-type]

    def test_write_context_and_write_result_validate_token_types(self) -> None:
        tenant = TenantId("acme")
        token = RevisionToken(tenant, Revision(3))

        context = WriteContext(tenant)
        result = WriteResult(token)

        assert context.tenant == tenant
        assert result.token == token
        assert result.tenant == tenant
        assert result.revision == Revision(3)
        with pytest.raises(TypeError, match="TenantId"):
            WriteContext("acme")  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="RevisionToken"):
            WriteResult(token.revision)  # type: ignore[arg-type]

    def test_read_context_exposes_token_and_validates_fields(self) -> None:
        tenant = TenantId("acme")
        revision = Revision(11)

        context = ReadContext(tenant, revision)

        assert context.token == RevisionToken(tenant, revision)
        with pytest.raises(TypeError, match="TenantId"):
            ReadContext("acme", revision)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="Revision"):
            ReadContext(tenant, 11)  # type: ignore[arg-type]


class TestRevisionConsistency:
    def test_relationship_change_carries_revision_token(self) -> None:
        token = RevisionToken(TenantId("acme"), Revision(7))
        tuple_ = RelationTuple.from_string("document:doc1#viewer@user:alice")

        change = RelationshipChange(
            token=token,
            relation_tuple=tuple_,
            operation=RelationshipOperation.WRITE,
        )

        assert change.token == token
        assert change.tenant == token.tenant
        assert change.revision == token.revision

        with pytest.raises(TypeError, match="RevisionToken"):
            RelationshipChange(
                token=token.revision,  # type: ignore[invalid-argument-type]
                relation_tuple=tuple_,
                operation=RelationshipOperation.WRITE,
            )

    def test_revision_for_consistency_resolves_head_and_freshness(self) -> None:
        tenant = TenantId("default")
        head = RevisionToken(tenant, Revision(3))
        fresh = RevisionToken(tenant, Revision(2))
        exact = RevisionToken(tenant, Revision(1))

        assert revision_for_consistency(head, None) == head
        assert revision_for_consistency(head, FullyConsistent()) == head
        assert revision_for_consistency(head, AtLeastAsFresh(fresh)) == head
        assert revision_for_consistency(head, AtExactRevision(exact)) == exact

        with pytest.raises(ValueError, match="newer than repository head"):
            revision_for_consistency(
                head, AtLeastAsFresh(RevisionToken(tenant, Revision(4)))
            )

    def test_revision_for_consistency_rejects_cross_tenant_token(self) -> None:
        head = RevisionToken(TenantId("acme"), Revision(3))
        other = RevisionToken(TenantId("globex"), Revision(1))

        with pytest.raises(ValueError, match="tenant does not match"):
            revision_for_consistency(head, AtLeastAsFresh(other))

        with pytest.raises(ValueError, match="tenant does not match"):
            revision_for_consistency(head, AtExactRevision(other))

    def test_consistency_wrappers_require_revision_tokens(self) -> None:
        with pytest.raises(TypeError, match="RevisionToken"):
            AtLeastAsFresh(Revision(1))  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="RevisionToken"):
            AtExactRevision(Revision(1))  # type: ignore[arg-type]

    def test_revision_for_consistency_rejects_unknown_policy(self) -> None:
        class UnknownConsistency(Consistency):
            pass

        head = RevisionToken(TenantId("acme"), Revision(3))

        with pytest.raises(TypeError, match="UnknownConsistency"):
            revision_for_consistency(head, UnknownConsistency())
