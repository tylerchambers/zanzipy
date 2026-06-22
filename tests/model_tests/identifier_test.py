import pytest

from zanzipy.models import (
    Identifier,
    IdentifierValidationError,
    NamespaceId,
    Relation,
)


class TestIdentifier:
    @pytest.mark.parametrize(
        "value",
        [
            "owner",
            "can_read",
            "_internal",
            "User123",
            "auth-service",
        ],
    )
    def test_valid_identifiers(self, value: str) -> None:
        assert str(Identifier(value)) == value

    # Chinese example invalid due to starting rule; hyphen allowed mid-string
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "1abc",
            "bad space",
            "bad:colon",
            "中文",
        ],
    )
    def test_invalid_identifiers(self, value: str) -> None:
        with pytest.raises(IdentifierValidationError):
            Identifier(value)

    def test_namespace_and_relation_aliases(self) -> None:
        assert str(NamespaceId("document")) == "document"
        assert str(Relation("viewer")) == "viewer"

    def test_string_representation(self) -> None:
        i = Identifier("owner")
        assert str(i) == "owner"
