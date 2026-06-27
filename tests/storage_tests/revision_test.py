import pytest

from zanzipy.models import RelationTuple
from zanzipy.storage.revision import (
    AtExactRevision,
    AtLeastAsFresh,
    FullyConsistent,
    RelationshipChange,
    RelationshipOperation,
    Revision,
    RevisionToken,
    TenantId,
    revision_for_consistency,
)


def test_revision_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Revision(-1)
    with pytest.raises(TypeError, match="int"):
        Revision(True)  # type: ignore[arg-type]


def test_tenant_id_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        TenantId("")
    with pytest.raises(TypeError, match="str"):
        TenantId(123)  # type: ignore[arg-type]


def test_relationship_change_carries_revision_token() -> None:
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
        RelationshipChange(  # type: ignore[arg-type]
            token=token.revision,
            relation_tuple=tuple_,
            operation=RelationshipOperation.WRITE,
        )


def test_revision_for_consistency_resolves_head_and_freshness() -> None:
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


def test_revision_for_consistency_rejects_cross_tenant_token() -> None:
    head = RevisionToken(TenantId("acme"), Revision(3))
    other = RevisionToken(TenantId("globex"), Revision(1))

    with pytest.raises(ValueError, match="tenant does not match"):
        revision_for_consistency(head, AtLeastAsFresh(other))

    with pytest.raises(ValueError, match="tenant does not match"):
        revision_for_consistency(head, AtExactRevision(other))
