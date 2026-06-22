from dataclasses import dataclass

from .identifier import Identifier


@dataclass(frozen=True, slots=True)
class NamespaceId(Identifier):
    """Namespace value object (inherits Identifier validation)."""
