import pytest

from zanzipy.models import EntityId, EntityIdValidationError


class TestEntityId:
    @pytest.mark.parametrize("value", ["readme", "user-123", "uuid_abc-def", "文档"])
    def test_valid_ids(self, value: str) -> None:
        assert str(EntityId(value)) == value

    @pytest.mark.parametrize(
        "value", ["bad space", "re@adme", "re:adme", "re#adme", ""]
    )  # empty invalid
    def test_invalid_ids(self, value: str) -> None:
        with pytest.raises(EntityIdValidationError):
            EntityId(value)

    def test_string_representation(self) -> None:
        e = EntityId("readme")
        assert str(e) == "readme"
