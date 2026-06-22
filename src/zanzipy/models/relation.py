from dataclasses import dataclass

from .identifier import Identifier


@dataclass(frozen=True, slots=True)
class Relation(Identifier):
    """Relation value object (inherits Identifier validation)."""
