from dataclasses import dataclass
import re
from typing import ClassVar

from .errors import EntityIdValidationError


@dataclass(frozen=True, slots=True)
class EntityId:
    """
    Valid entity id for object_id and subject_id.

    Rules:
    - Cannot contain '#', '@', ':', or whitespace
    - Otherwise may contain any unicode characters
    """

    _ID_CHARS: ClassVar[re.Pattern] = re.compile(r"^[^#@:\s]+$")

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise EntityIdValidationError("id cannot be empty")
        if not self._ID_CHARS.match(self.value):
            raise EntityIdValidationError(
                "id cannot contain '#', '@', ':', or whitespace characters"
            )

    def __str__(self) -> str:
        return self.value
