from dataclasses import dataclass
from typing import Self

from .errors import InvalidTupleFormatError
from .object import Obj
from .relation import Relation
from .subject import Subject


@dataclass(frozen=True, slots=True)
class RelationTuple:
    """
    Represents a Zanzibar relationship tuple.

    Format:
        `object_namespace:object_id#relation@subject_namespace:subject_id[#subject_relation]`

    Constraints:
        - Namespaces and relations must be valid identifiers (alphanumeric, underscore,
          or hyphen, starting with letter or underscore)
        - IDs may contain any characters except `#`, `@`, `:`, and whitespace
        - subject_relation follows the same rules as relation
        - No component may be empty

    Examples:
        - `document:readme#owner@user:alice`
        - `folder:docs#viewer@group:eng#member`
        - `doc:uuid-123-abc#can_read@user:bob`

    Attributes:
        object: The object being related (contains namespace and id)
        relation: The relation name (e.g., 'owner', 'viewer', 'can_read')
        subject: The subject entity (contains namespace, id, and optional
            relation for subject sets)

    Note:
        Instances are immutable and hashable, suitable for use in sets and as dict keys.
    """

    object: Obj
    relation: Relation
    subject: Subject

    def __post_init__(self) -> None:
        if not isinstance(self.object, Obj):
            raise TypeError("relation tuple object must be an Obj")
        if not isinstance(self.relation, Relation):
            raise TypeError("relation tuple relation must be a Relation")
        if not isinstance(self.subject, Subject):
            raise TypeError("relation tuple subject must be a Subject")

    @staticmethod
    def _invalid_format_error(tuple_string: str) -> InvalidTupleFormatError:
        return InvalidTupleFormatError(
            f"Invalid tuple format: '{tuple_string}'. "
            "Expected: 'object_namespace:object_id#relation@subject_namespace:"
            "subject_id[#subject_relation]'. "
            "Namespaces and relations must be valid identifiers "
            "(letters/digits/_/-, start with letter/_). "
            "IDs may contain any characters except '#', '@', ':', and whitespace. "
            "No component may be empty."
        )

    @classmethod
    def from_parts(
        cls,
        object: Obj | str,
        relation: Relation | str,
        subject: Subject | str,
    ) -> Self:
        obj = object if isinstance(object, Obj) else Obj.from_string(object)
        rel = relation if isinstance(relation, Relation) else Relation(relation)
        subj = subject if isinstance(subject, Subject) else Subject.from_string(subject)
        return cls(obj, rel, subj)

    @classmethod
    def from_strings(cls, object_str: str, relation: str, subject_str: str) -> Self:
        tuple_string = f"{object_str}#{relation}@{subject_str}"
        try:
            return cls.from_parts(object_str, relation, subject_str)
        except ValueError as exc:
            raise cls._invalid_format_error(tuple_string) from exc

    @classmethod
    def from_string(cls, tuple_string: str) -> RelationTuple:
        """Parse a Zanzibar relation tuple string.

        Args:
            tuple_string:
                String in format
                `object_namespace:object_id#relation@subject_namespace:subject_id[#subject_relation]`

        Returns:
            RelationTuple instance

        Raises:
            InvalidTupleFormatError: If the string doesn't match the expected format

        Examples:
            >>> RelationTuple.from_string("document:readme#owner@user:alice")
            >>> RelationTuple.from_string("folder:docs#viewer@group:eng#member")
        """
        object_and_relation, at_separator, subject = tuple_string.partition("@")
        object, relation_separator, relation = object_and_relation.partition("#")
        if at_separator == "" or relation_separator == "":
            raise cls._invalid_format_error(tuple_string)

        try:
            return cls.from_parts(object, relation, subject)
        except ValueError as exc:
            raise cls._invalid_format_error(tuple_string) from exc

    def to_dict(self) -> dict:
        """Return a dictionary representation of the tuple.

        Structure:
            {
              "object": {"namespace": str, "id": str},
              "relation": str,
              "subject": {"namespace": str, "id": str, "relation": Optional[str]}
            }
        """
        return {
            "object": self.object.to_dict(),
            "relation": str(self.relation),
            "subject": self.subject.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Create a RelationTuple from a dictionary (nested structure)."""
        return cls(
            object=Obj.from_dict(data["object"]),
            relation=Relation(data["relation"]),
            subject=Subject.from_dict(data["subject"]),
        )

    def __str__(self) -> str:
        """Return canonical string representation of the tuple."""
        object_str = str(self.object)
        subject_str = str(self.subject)
        return f"{object_str}#{self.relation}@{subject_str}"

    def __repr__(self) -> str:
        """Return detailed representation for debugging."""
        subject_relation_repr = (
            str(self.subject.relation) if self.subject.relation is not None else None
        )
        return (
            f"RelationTuple("
            f"object_namespace={str(self.object.namespace)!r}, "
            f"object_id={str(self.object.id)!r}, "
            f"relation={str(self.relation)!r}, "
            f"subject_namespace={str(self.subject.namespace)!r}, "
            f"subject_id={str(self.subject.id)!r}, "
            f"subject_relation={subject_relation_repr!r})"
        )
