from typing import TYPE_CHECKING, Self

from zanzipy.models import Relation as Rel

from .rules import RewriteRule
from .subjects import SubjectReference
from .types import SchemaDefinitionType

if TYPE_CHECKING:
    from collections.abc import Iterable


class RelationDef:
    """Relation definition with allowed subjects and optional rewrite.

    Relations must declare at least one allowed subject reference; names are
    validated with the same rules as tuple relation names.
    """

    __slots__ = ("_allowed_subjects", "_description", "_name", "_rewrite")

    def __init__(
        self,
        *,
        name: str,
        allowed_subjects: SubjectReference | Iterable[SubjectReference],
        rewrite: RewriteRule | None = None,
        description: str | None = None,
    ) -> None:
        """Create a relation definition and normalize allowed subjects.

        Raises:
            ValueError: If no allowed subject references are declared.
            TypeError: If any allowed subject is not a ``SubjectReference``.
        """
        # Validate relation name
        Rel(name)
        normalized = self._normalize_subject_references(allowed_subjects)
        if not normalized:
            raise ValueError("Relation must declare at least one allowed subject type")

        self._name = name
        self._allowed_subjects = normalized
        self._rewrite = rewrite
        self._description = description

    @property
    def name(self) -> str:
        """Return the validated relation name."""
        return self._name

    @property
    def allowed_subjects(self) -> tuple[SubjectReference, ...]:
        """Return allowed subject references as an immutable tuple."""
        return self._allowed_subjects

    @property
    def rewrite(self) -> RewriteRule | None:
        """Return the rewrite rule, or ``None`` for direct stored tuples."""
        return self._rewrite

    @property
    def description(self) -> str | None:
        """Return the optional human-readable relation description."""
        return self._description

    @staticmethod
    def _normalize_subject_references(
        subjects: SubjectReference | Iterable[SubjectReference],
    ) -> tuple[SubjectReference, ...]:
        """Coerce one or more subject refs into an immutable tuple."""
        if isinstance(subjects, SubjectReference):
            return (subjects,)
        normalized = tuple(subjects)
        for subject in normalized:
            if not isinstance(subject, SubjectReference):
                raise TypeError("allowed_subjects must contain SubjectReference values")
        return normalized

    @classmethod
    def with_subjects(
        cls,
        name: str,
        subjects: SubjectReference | Iterable[SubjectReference],
        rewrite: RewriteRule | None = None,
        description: str | None = None,
    ) -> RelationDef:
        """Create a relation definition from one or more subject references."""
        return cls(
            name=name,
            allowed_subjects=subjects,
            rewrite=rewrite,
            description=description,
        )

    def to_dict(self) -> dict:
        """Serialize the relation to its canonical schema dictionary."""
        return {
            "type": SchemaDefinitionType.RELATION,
            "name": self.name,
            "allowed_subjects": [s.to_dict() for s in self.allowed_subjects],
            "rewrite": self.rewrite.to_dict() if self.rewrite else None,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Deserialize a relation definition from its schema dictionary."""
        return cls(
            name=data["name"],
            allowed_subjects=[
                SubjectReference.from_dict(s) for s in data["allowed_subjects"]
            ],
            rewrite=RewriteRule.from_dict(data["rewrite"]) if data["rewrite"] else None,
            description=data.get("description"),
        )
