"""Lightweight integration layer to expose a domain-friendly engine API.

This wraps the existing ZanzibarClient to provide methods consumed by mixins
without changing the client's behavior or public API.
"""

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

from .models import Obj, TupleFilter

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .client import ZanzibarClient
    from .engine.expander import SubjectSet
    from .models import Subject
    from .schema.registry import SchemaRegistry
    from .storage.repos.abstract.relations import RelationRepository
    from .storage.revision import Consistency, WriteResult


class ZanzibarEngine:
    """Engine façade used by mixins and domain integrations.

    Delegates to an underlying ZanzibarClient instance.
    """

    def __init__(self, client: ZanzibarClient) -> None:
        """Wrap a configured client without changing its storage behavior."""
        self._client = client

    @property
    def schema(self) -> SchemaRegistry:
        """Return the schema registry used by the underlying client."""
        return self._client.schema

    @property
    def relations_repository(self) -> RelationRepository:
        """Return the relation repository used by the underlying client."""
        return self._client.relations_repository

    def write_tuple(
        self,
        *,
        subject: Subject,
        relation: str,
        resource: Obj,
    ) -> WriteResult:
        """Persist a subject-relation-resource tuple through the client."""
        return self._client.write(str(resource), relation, str(subject))

    def delete_tuple(
        self, *, subject: Subject, relation: str, resource: Obj
    ) -> WriteResult:
        """Remove a subject-relation-resource tuple through the client."""
        return self._client.delete(str(resource), relation, str(subject))

    def check(
        self,
        *,
        subject: Subject,
        permission: str,
        resource: Obj,
        consistency: Consistency | None = None,
    ) -> bool:
        """Return whether a subject has a permission on a resource."""
        return self._client.check(
            str(resource),
            permission,
            str(subject),
            consistency=consistency,
        )

    def expand(
        self,
        *,
        permission: str,
        resource: Obj,
        consistency: Consistency | None = None,
    ) -> SubjectSet:
        """Expand a resource permission into the matching subject set."""
        return self._client.expand(
            str(resource),
            permission,
            consistency=consistency,
        )

    def list_resources(
        self,
        *,
        subject: Subject,
        permission: str,
        resource_type: str,
        limit: int | None = None,
        consistency: Consistency | None = None,
    ) -> list[Obj]:
        """List resources of a type that a subject can access."""
        objects = self._client.list_objects(
            resource_type,
            permission,
            str(subject),
            consistency=consistency,
        )
        if limit is not None:
            objects = objects[:limit]
        return [Obj.from_string(o) for o in objects]

    def read_tuples(
        self,
        *,
        subject: Subject | None = None,
        resource: Obj | None = None,
        consistency: Consistency | None = None,
    ) -> Iterable:
        """Read tuples constrained by subject and/or resource.

        Raises:
            ValueError: If neither subject nor resource is provided.
        """
        if subject is None and resource is None:
            raise ValueError("At least one of subject or resource must be provided")
        filt = TupleFilter.from_parts(obj=resource, subject=subject)
        return self._client.read_tuples(filt, consistency=consistency)

    def list_direct_subjects(self, *, resource: Obj, relation: str) -> list[str]:
        """List canonical direct subject strings for a resource relation."""
        return self._client.list_subjects_direct(str(resource), relation)

    def get_schema(self, namespace: str) -> dict:
        """Return the serialized schema definition for a namespace."""
        # Return the namespace definition dict for convenience
        return self._client.schema.get_namespace(namespace).to_dict()


_engine_ctx: ContextVar[ZanzibarEngine] = ContextVar("zanzipy_engine")


def configure_authorization(engine: ZanzibarEngine) -> Token[ZanzibarEngine]:
    """Bind the authorization engine to the current context.

    The returned token can be passed to ``reset_authorization`` to restore the
    previous binding. Existing bootstrap code may ignore the token when a single
    process-wide engine is intended.
    """

    return _engine_ctx.set(engine)


def reset_authorization(token: Token[ZanzibarEngine]) -> None:
    """Restore the engine binding captured by ``configure_authorization``."""

    _engine_ctx.reset(token)


def get_authorization_engine() -> ZanzibarEngine:
    """Return the engine bound to the current execution context.

    Raises:
        RuntimeError: If no engine has been configured for this context.
    """
    try:
        return _engine_ctx.get()
    except LookupError as exc:
        raise RuntimeError(
            "Authorization engine not configured. Call configure_authorization() first."
        ) from exc
