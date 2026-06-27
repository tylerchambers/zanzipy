"""Tenant, revision, mutation, and consistency values for relation storage."""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zanzipy.models import RelationTuple


@dataclass(frozen=True, slots=True)
class TenantId:
    """Identifier for one authorization universe."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise TypeError("tenant id must be a str")
        if not self.value:
            raise ValueError("tenant id must not be empty")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True, slots=True)
class Revision:
    """Monotonic datastore revision token within one tenant."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise TypeError("revision value must be an int")
        if self.value < 0:
            raise ValueError("revision value must be non-negative")

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class RevisionToken:
    """Tenant-scoped revision token."""

    tenant: TenantId
    revision: Revision

    def __post_init__(self) -> None:
        if not isinstance(self.tenant, TenantId):
            raise TypeError("revision token tenant must be a TenantId")
        if not isinstance(self.revision, Revision):
            raise TypeError("revision token revision must be a Revision")


@dataclass(frozen=True, slots=True)
class WriteContext:
    """Tenant context for relation tuple mutations."""

    tenant: TenantId

    def __post_init__(self) -> None:
        if not isinstance(self.tenant, TenantId):
            raise TypeError("write context tenant must be a TenantId")


@dataclass(frozen=True, slots=True)
class ReadContext:
    """Tenant and revision context for repository reads and evaluations."""

    tenant: TenantId
    revision: Revision

    def __post_init__(self) -> None:
        if not isinstance(self.tenant, TenantId):
            raise TypeError("read context tenant must be a TenantId")
        if not isinstance(self.revision, Revision):
            raise TypeError("read context revision must be a Revision")

    @property
    def token(self) -> RevisionToken:
        """Return the tenant-scoped revision token for this context."""

        return RevisionToken(self.tenant, self.revision)


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Result returned by tuple mutations."""

    token: RevisionToken

    def __post_init__(self) -> None:
        if not isinstance(self.token, RevisionToken):
            raise TypeError("write result token must be a RevisionToken")

    @property
    def tenant(self) -> TenantId:
        """Return the tenant that owns the committed revision."""

        return self.token.tenant

    @property
    def revision(self) -> Revision:
        """Return the committed revision within ``tenant``."""

        return self.token.revision


class RelationshipOperation(StrEnum):
    """Type of tuple change stored in a revision stream."""

    WRITE = "write"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class TupleMutation:
    """One relation tuple mutation inside a repository write batch."""

    relation_tuple: RelationTuple
    operation: RelationshipOperation

    @classmethod
    def touch(cls, relation_tuple: RelationTuple) -> TupleMutation:
        """Create or keep ``relation_tuple`` active."""

        return cls(relation_tuple=relation_tuple, operation=RelationshipOperation.WRITE)

    @classmethod
    def delete(cls, relation_tuple: RelationTuple) -> TupleMutation:
        """Mark ``relation_tuple`` inactive if it is active."""

        return cls(
            relation_tuple=relation_tuple,
            operation=RelationshipOperation.DELETE,
        )


@dataclass(frozen=True, slots=True)
class RelationshipChange:
    """One tuple change committed at a tenant-scoped revision token."""

    token: RevisionToken
    relation_tuple: RelationTuple
    operation: RelationshipOperation

    def __post_init__(self) -> None:
        if not isinstance(self.token, RevisionToken):
            raise TypeError("relationship change token must be a RevisionToken")

    @property
    def tenant(self) -> TenantId:
        """Return the tenant that owns the changed revision."""

        return self.token.tenant

    @property
    def revision(self) -> Revision:
        """Return the tenant-local revision for compatibility."""

        return self.token.revision


class Consistency:
    """Marker base class for read consistency policies."""


@dataclass(frozen=True, slots=True)
class FullyConsistent(Consistency):
    """Read at the tenant's repository head revision."""


@dataclass(frozen=True, slots=True)
class AtLeastAsFresh(Consistency):
    """Read from a tenant token at least as fresh as ``token``."""

    token: RevisionToken

    def __post_init__(self) -> None:
        if not isinstance(self.token, RevisionToken):
            raise TypeError("at-least-as-fresh consistency requires a RevisionToken")


@dataclass(frozen=True, slots=True)
class AtExactRevision(Consistency):
    """Read exactly at ``token``."""

    token: RevisionToken

    def __post_init__(self) -> None:
        if not isinstance(self.token, RevisionToken):
            raise TypeError("exact-revision consistency requires a RevisionToken")


def revision_for_consistency(
    head_token: RevisionToken,
    consistency: Consistency | None,
) -> RevisionToken:
    """Resolve a consistency policy to the tenant-scoped token used for a read."""

    if consistency is None or isinstance(consistency, FullyConsistent):
        return head_token
    if isinstance(consistency, AtLeastAsFresh):
        _raise_if_token_tenant_mismatch(head_token, consistency.token)
        if head_token.revision < consistency.token.revision:
            raise ValueError(
                "requested revision is newer than repository head: "
                f"requested {consistency.token.revision}, head {head_token.revision}"
            )
        return head_token
    if isinstance(consistency, AtExactRevision):
        _raise_if_token_tenant_mismatch(head_token, consistency.token)
        return consistency.token
    raise TypeError(f"unknown consistency policy: {type(consistency).__name__}")


def _raise_if_token_tenant_mismatch(
    head_token: RevisionToken,
    requested_token: RevisionToken,
) -> None:
    if head_token.tenant != requested_token.tenant:
        raise ValueError(
            "consistency token tenant does not match repository tenant: "
            f"requested {requested_token.tenant}, head {head_token.tenant}"
        )
