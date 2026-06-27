from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from .namespace import NamespaceId
from .relation import Relation
from .subject import Subject

if TYPE_CHECKING:
    from .object import Obj


@dataclass(frozen=True, slots=True)
class LookupResourcesRequest:
    """Engine request for reverse LookupResources evaluation.

    ``resource_type`` names the namespace to return, ``permission`` names the
    relation or permission to evaluate on that namespace, and ``subject`` is the
    direct subject whose authorized resources should be enumerated. Subject-set
    subjects are rejected because LookupResources answers for concrete subjects.
    """

    resource_type: str
    permission: str
    subject: Subject

    def __post_init__(self) -> None:
        self.to_resource_namespace()
        self.to_permission()
        if not isinstance(self.subject, Subject):
            raise TypeError("lookup subject must be a Subject")
        self.subject.require_direct()

    @classmethod
    def from_strings(
        cls,
        resource_type: str,
        permission: str,
        subject: str,
    ) -> Self:
        """Construct a lookup request from public string API components.

        The subject string must be a canonical direct subject such as
        ``"user:alice"``; subject-set strings such as ``"group:eng#member"``
        raise ``ValueError``.
        """

        return cls(
            resource_type=resource_type,
            permission=permission,
            subject=Subject.from_string(subject),
        )

    def to_resource_namespace(self) -> NamespaceId:
        """Return the requested resource namespace as a validated value."""

        return NamespaceId(self.resource_type)

    def to_permission(self) -> Relation:
        """Return the requested permission or relation as a validated value."""

        return Relation(self.permission)


@dataclass(frozen=True, slots=True)
class LookupResourcesResponse:
    """Engine LookupResources result with traversal diagnostics.

    ``resources`` contains the sorted authorized resource objects. ``debug_trace``
    is populated only when the authorization engine is created with debug mode
    enabled. ``depth_reached`` and ``tuples_examined`` are best-effort traversal
    counters collected by lookup and any canonical check filters it invokes.
    """

    resources: tuple[Obj, ...]
    debug_trace: tuple[str, ...] | None = None
    depth_reached: int = 0
    tuples_examined: int = 0
