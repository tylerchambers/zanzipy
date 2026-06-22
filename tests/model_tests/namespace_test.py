import pytest

from zanzipy.models import IdentifierValidationError, NamespaceId


class TestNamespace:
    def test_valid(self) -> None:
        assert str(NamespaceId("document")) == "document"

    @pytest.mark.parametrize("value", ["", "  ", "1bad", "bad space"])
    def test_invalid(self, value: str) -> None:
        with pytest.raises(IdentifierValidationError):
            NamespaceId(value)

    def test_string_representation(self) -> None:
        n = NamespaceId("document")
        assert str(n) == "document"
