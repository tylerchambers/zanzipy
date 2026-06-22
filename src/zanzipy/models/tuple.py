from dataclasses import dataclass
import re
from typing import ClassVar, Self

from .errors import InvalidTupleFormatError
from .id import EntityId
from .namespace import NamespaceId
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

    # Complete tuple parsing pattern
    _TUPLE_PATTERN: ClassVar[re.Pattern] = re.compile(
        r"^(?P<object_namespace>[a-zA-Z_][a-zA-Z0-9_-]*)"  # object namespace
        r":(?P<object_id>[^#@:\s]+)"  # object id
        r"#(?P<relation>[a-zA-Z_][a-zA-Z0-9_-]*)"  # relation
        r"@(?P<subject_namespace>[a-zA-Z_][a-zA-Z0-9_-]*)"  # subject namespace
        r":(?P<subject_id>[^#@:\s]+)"  # subject id
        r"(?:#(?P<subject_relation>[a-zA-Z_][a-zA-Z0-9_-]*))?$"  # optional subject rel
    )

    object: Obj
    relation: Relation
    subject: Subject

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
        match = cls._TUPLE_PATTERN.match(tuple_string)
        if not match:
            raise InvalidTupleFormatError(
                f"Invalid tuple format: '{tuple_string}'. "
                "Expected: 'object_namespace:object_id#relation@subject_namespace:"
                "subject_id[#subject_relation]'. "
                "Namespaces and relations must be valid identifiers "
                "(letters/digits/_/-, start with letter/_). "
                "IDs may contain any characters except '#', '@', ':', and whitespace. "
                "No component may be empty."
            )

        groups = match.groupdict()
        subject_relation = groups["subject_relation"]
        return cls(
            object=Obj(
                NamespaceId(groups["object_namespace"]),
                EntityId(groups["object_id"]),
            ),
            relation=Relation(groups["relation"]),
            subject=Subject(
                NamespaceId(groups["subject_namespace"]),
                EntityId(groups["subject_id"]),
                Relation(subject_relation) if subject_relation is not None else None,
            ),
        )

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
        subj = data["subject"]
        # Treat presence of empty string subject relation as invalid
        if isinstance(subj, dict) and "relation" in subj and subj["relation"] == "":
            _ = Relation("")  # will raise IdentifierValidationError
        return cls(
            object=Obj.from_dict(data["object"]),
            relation=Relation(data["relation"]),
            subject=Subject.from_dict(subj),
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
