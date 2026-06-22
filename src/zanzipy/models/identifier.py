from dataclasses import dataclass
import re
from typing import ClassVar

from .errors import IdentifierValidationError


@dataclass(frozen=True, slots=True)
class Identifier:
    """
    Valid Zanzibar identifier for namespaces and relations.

    Rules:
    - Start with a letter or underscore
    - Contain letters, digits, underscores, or hyphens
    """

    _IDENTIFIER: ClassVar[re.Pattern] = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise IdentifierValidationError("identifier cannot be empty")
        if not self._IDENTIFIER.match(self.value):
            raise IdentifierValidationError(
                "identifier must be a valid identifier (letters/digits/_/-, start with "
                "letter/_)"
            )

    def __str__(self) -> str:
        return self.value
