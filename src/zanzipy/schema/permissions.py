from dataclasses import dataclass
from typing import Self

from zanzipy.models import Relation as Rel

from .rules import RewriteRule
from .types import SchemaDefinitionType


@dataclass(frozen=True, slots=True)
class PermissionDef:
    """Computed permission definition backed by a rewrite expression."""

    name: str
    rewrite: RewriteRule
    description: str | None = None

    def __post_init__(self) -> None:
        # Validate permission name as a relation identifier
        Rel(self.name)

    def to_dict(self) -> dict:
        """Serialize the permission to its canonical schema dictionary."""
        return {
            "type": SchemaDefinitionType.PERMISSION,
            "name": self.name,
            "rewrite": self.rewrite.to_dict(),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Deserialize a permission definition from its schema dictionary."""
        return cls(
            name=data["name"],
            rewrite=RewriteRule.from_dict(data["rewrite"]),
            description=data.get("description"),
        )
