from typing import TYPE_CHECKING, Self

from zanzipy.models.relation import Relation as Rel

from .rules import RewriteRule
from .subjects import SubjectReference

if TYPE_CHECKING:
    from collections.abc import Iterable


class RelationDef:
    """Defines a relation: allowed subject types and optional rewrite.

    Note: Relations must declare at least one allowed subject.
    """

    __slots__ = ("_allowed_subjects", "_description", "_name", "_rewrite")

    def __init__(
        self,
        *,
        name: str,
        allowed_subjects: tuple[SubjectReference, ...] | SubjectReference,
        rewrite: RewriteRule | None = None,
        description: str | None = None,
    ) -> None:
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
        return self._name

    @property
    def allowed_subjects(self) -> tuple[SubjectReference, ...]:
        return self._allowed_subjects

    @property
    def rewrite(self) -> RewriteRule | None:
        return self._rewrite

    @property
    def description(self) -> str | None:
        return self._description

    @staticmethod
    def _normalize_subject_references(
        subjects: SubjectReference | tuple[SubjectReference, ...],
    ) -> tuple[SubjectReference, ...]:
        """Coerce single or iterable subject refs into a tuple.

        Ensures a stable internal representation regardless of input form.
        """
        if isinstance(subjects, SubjectReference):
            return (subjects,)
        return subjects

    @classmethod
    def with_subjects(
        cls,
        name: str,
        subjects: SubjectReference | Iterable[SubjectReference],
        rewrite: RewriteRule | None = None,
        description: str | None = None,
    ) -> RelationDef:
        return cls(
            name=name,
            allowed_subjects=subjects,
            rewrite=rewrite,
            description=description,
        )

    def to_dict(self) -> dict:
        return {
            "type": "relation",
            "name": self.name,
            "allowed_subjects": [s.to_dict() for s in self.allowed_subjects],
            "rewrite": self.rewrite.to_dict() if self.rewrite else None,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        return cls(
            name=data["name"],
            allowed_subjects=[
                SubjectReference.from_dict(s) for s in data["allowed_subjects"]
            ],
            rewrite=RewriteRule.from_dict(data["rewrite"]) if data["rewrite"] else None,
            description=data["description"],
        )
