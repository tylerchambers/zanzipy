import pytest

from zanzipy.storage.revision import (
    AtExactRevision,
    AtLeastAsFresh,
    FullyConsistent,
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
