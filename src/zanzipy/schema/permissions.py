from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from zanzipy.models.relation import Relation as Rel

if TYPE_CHECKING:
    from .rules import RewriteRule
else:
    # For runtime use in from_dict
    from .rules import RewriteRule


@dataclass(frozen=True, slots=True)
class PermissionDef:
    """Defines a computed permission: rewrite expression only."""

    name: str
    rewrite: RewriteRule
    description: str | None = None

    def __post_init__(self) -> None:
        # Validate permission name as a relation identifier
        Rel(self.name)

    def to_dict(self) -> dict:
        return {
            "type": "permission",
            "name": self.name,
            "rewrite": self.rewrite.to_dict(),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        return cls(
            name=data["name"],
            rewrite=RewriteRule.from_dict(data["rewrite"]),
            description=data["description"],
        )
