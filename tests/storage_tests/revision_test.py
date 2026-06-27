import pytest

from zanzipy.storage.revision import (
    AtExactRevision,
    AtLeastAsFresh,
    FullyConsistent,
    Revision,
    revision_for_consistency,
)


def test_revision_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Revision(-1)
    with pytest.raises(TypeError, match="int"):
        Revision(True)  # type: ignore[arg-type]


def test_revision_for_consistency_resolves_head_and_freshness() -> None:
    head = Revision(3)

    assert revision_for_consistency(head, None) == head
    assert revision_for_consistency(head, FullyConsistent()) == head
    assert revision_for_consistency(head, AtLeastAsFresh(Revision(2))) == head
    assert revision_for_consistency(head, AtExactRevision(Revision(1))) == Revision(1)

    with pytest.raises(ValueError, match="newer than repository head"):
        revision_for_consistency(head, AtLeastAsFresh(Revision(4)))
