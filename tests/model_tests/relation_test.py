import pytest

from zanzipy.models import IdentifierValidationError, Relation


class TestRelation:
    def test_valid(self) -> None:
        assert str(Relation("owner")) == "owner"

    @pytest.mark.parametrize("value", ["", "1bad", "bad space", "bad:colon"])
    def test_invalid(self, value: str) -> None:
        with pytest.raises(IdentifierValidationError):
            Relation(value)

    def test_string_representation(self) -> None:
        r = Relation("owner")
        assert str(r) == "owner"
