import pytest

from zanzipy.models.errors import IdentifierValidationError
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.rules import ComputedUsersetRule, UnionRule


class TestPermissionDef:
    def test_to_dict_minimal(self) -> None:
        perm = PermissionDef(name="can_view", rewrite=ComputedUsersetRule("viewer"))
        assert perm.to_dict() == {
            "type": "permission",
            "name": "can_view",
            "rewrite": {"type": "computed_userset", "relation": "viewer"},
            "description": None,
        }

    def test_to_dict_with_description(self) -> None:
        perm = PermissionDef(
            name="can_edit",
            rewrite=ComputedUsersetRule("editor"),
            description="Editors may edit",
        )
        assert perm.to_dict() == {
            "type": "permission",
            "name": "can_edit",
            "rewrite": {"type": "computed_userset", "relation": "editor"},
            "description": "Editors may edit",
        }

    def test_nested_rewrite_serialization(self) -> None:
        rewrite = UnionRule(
            children=(ComputedUsersetRule("owner"), ComputedUsersetRule("editor"))
        )
        perm = PermissionDef(name="can_manage", rewrite=rewrite)
        assert perm.to_dict() == {
            "type": "permission",
            "name": "can_manage",
            "rewrite": {
                "type": "union",
                "children": [
                    {"type": "computed_userset", "relation": "owner"},
                    {"type": "computed_userset", "relation": "editor"},
                ],
            },
            "description": None,
        }

    @pytest.mark.parametrize(
        "name",
        [
            "can_view",
            "A",
            "_abc",
            "can-view-1",
        ],
    )
    def test_valid_names(self, name: str) -> None:
        # Should not raise; construction validates via Relation(name)
        perm = PermissionDef(name=name, rewrite=ComputedUsersetRule("viewer"))
        assert perm.name == name

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "1starts_with_digit",
            "contains space",
            "bang!",
        ],
    )
    def test_invalid_names(self, name: str) -> None:
        with pytest.raises(IdentifierValidationError):
            PermissionDef(name=name, rewrite=ComputedUsersetRule("viewer"))
