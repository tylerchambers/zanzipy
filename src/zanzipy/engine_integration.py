"""Lightweight integration layer to expose a domain-friendly engine API.

This wraps the existing ZanzibarClient to provide methods consumed by mixins
without changing the client's behavior or public API.
"""

from contextvars import ContextVar
from typing import TYPE_CHECKING

from .models.filter import TupleFilter
from .models.object import Obj

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .client import ZanzibarClient
    from .engine.expander import SubjectSet
    from .models.subject import Subject
    from .schema.registry import SchemaRegistry
    from .storage.repos.abstract.relations import RelationRepository


class ZanzibarEngine:
    """Engine façade used by mixins and domain integrations.

    Delegates to an underlying ZanzibarClient instance.
    """

    def __init__(self, client: ZanzibarClient) -> None:
        self._client = client

    @property
    def schema(self) -> SchemaRegistry:
        return self._client.schema

    @property
    def relations_repository(self) -> RelationRepository:
        return self._client.relations_repository

    def write_tuple(self, *, subject: Subject, relation: str, resource: Obj) -> None:
        self._client.write(str(resource), relation, str(subject))

    def delete_tuple(self, *, subject: Subject, relation: str, resource: Obj) -> bool:
        return self._client.delete(str(resource), relation, str(subject))

    def check(self, *, subject: Subject, permission: str, resource: Obj) -> bool:
        return self._client.check(str(resource), permission, str(subject))

    def expand(self, *, permission: str, resource: Obj) -> SubjectSet:
        return self._client.expand(str(resource), permission)

    def list_resources(
        self,
        *,
        subject: Subject,
        permission: str,
        resource_type: str,
        limit: int | None = None,
    ) -> list[Obj]:
        objects = self._client.list_objects(resource_type, permission, str(subject))
        if limit is not None:
            objects = objects[:limit]
        return [Obj.from_string(o) for o in objects]

    def read_tuples(
        self,
        *,
        subject: Subject | None = None,
        resource: Obj | None = None,
    ) -> Iterable:
        if subject is None and resource is None:
            raise ValueError("At least one of subject or resource must be provided")
        filt = TupleFilter.from_parts(obj=resource, subject=subject)
        return self._client.relations_repository.read(filt)

    def list_direct_subjects(self, *, resource: Obj, relation: str) -> list[str]:
        return self._client.list_subjects_direct(str(resource), relation)

    def get_schema(self, namespace: str) -> dict:
        # Return the namespace definition dict for convenience
        return self._client.schema.get_namespace(namespace).to_dict()


_engine_ctx: ContextVar[ZanzibarEngine] = ContextVar("zanzipy_engine")


def configure_authorization(engine: ZanzibarEngine) -> None:
    """Configure the global/context engine used by mixins.

    Prefer calling this at application bootstrap or within a request context.
    """

    _engine_ctx.set(engine)


def get_authorization_engine() -> ZanzibarEngine:
    try:
        return _engine_ctx.get()
    except LookupError as exc:
        raise RuntimeError(
            "Authorization engine not configured. Call configure_authorization() first."
        ) from exc
