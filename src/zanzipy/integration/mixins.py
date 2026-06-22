"""Domain mixins for resource, subject, and group integration.

These ABC-style mixins provide concrete authorization helpers while requiring
domain models to implement only minimal reference methods.
"""

from typing import TYPE_CHECKING

from zanzipy.engine_integration import get_authorization_engine

if TYPE_CHECKING:
    from collections.abc import Iterable

    from zanzipy.engine.expander import SubjectSet
    from zanzipy.models.object import Obj
    from zanzipy.models.subject import Subject


class AuthorizableResource:
    """Mixin for domain objects that can be accessed/protected."""

    def get_resource_dict(self) -> dict:
        """Return {'namespace': str, 'id': Any} (and optional 'relation').

        Implement this OR override get_resource_ref(). If implemented, the
        default get_resource_ref() will build an Obj from this dict.
        """
        raise NotImplementedError

    def get_resource_ref(self) -> Obj:
        """Return the Zanzibar object reference for this resource."""
        # Default: build from dict if provided
        try:
            raw = self.get_resource_dict()
        except NotImplementedError as exc:  # no dict implementation provided
            raise NotImplementedError from exc
        from zanzipy.models.object import Obj as _Obj

        return _Obj.from_dict(_coerce_obj_dict(raw))

    # Concrete helper methods
    def grant(self, subject: object, relation: str) -> None:
        engine = get_authorization_engine()
        subject_ref = _normalize_to_subject(subject)
        engine.write_tuple(
            subject=subject_ref,
            relation=relation,
            resource=self.get_resource_ref(),
        )

    def revoke(self, subject: object, relation: str) -> None:
        engine = get_authorization_engine()
        subject_ref = _normalize_to_subject(subject)
        engine.delete_tuple(
            subject=subject_ref,
            relation=relation,
            resource=self.get_resource_ref(),
        )

    def check(self, subject: AuthorizableSubject, permission: str) -> bool:
        engine = get_authorization_engine()
        return engine.check(
            subject=subject.get_subject_ref(),
            permission=permission,
            resource=self.get_resource_ref(),
        )

    def who_can(self, permission: str) -> list[Subject]:
        engine = get_authorization_engine()
        subject_set: SubjectSet = engine.expand(
            permission=permission, resource=self.get_resource_ref()
        )
        # Convert canonical strings to Subject objects
        from zanzipy.models.subject import Subject as _Subject

        results: list[_Subject] = []
        for s in sorted(subject_set.users | subject_set.usersets):
            results.append(_Subject.from_string(s))
        return results

    def get_permissions(self, subject: AuthorizableSubject) -> set[str]:
        engine = get_authorization_engine()
        obj = self.get_resource_ref()
        ns = engine.schema.get_namespace(str(obj.namespace))
        permission_names = tuple(ns.permissions.keys())
        return {name for name in permission_names if self.check(subject, name)}


class AuthorizableSubject:
    """Mixin for domain objects that can access resources (users, groups)."""

    def get_subject_dict(self) -> dict:
        """Return {'namespace': str, 'id': Any, 'relation': optional str}.

        Implement this OR override get_subject_ref(). If implemented, the
        default get_subject_ref() will build a Subject from this dict.
        """
        raise NotImplementedError

    def get_subject_ref(self) -> Subject:
        """Return the Zanzibar subject reference."""
        # Default: build from dict if provided
        try:
            raw = self.get_subject_dict()
        except NotImplementedError as exc:  # no dict implementation provided
            raise NotImplementedError from exc
        from zanzipy.models.subject import Subject as _Subject

        return _Subject.from_dict(_coerce_subject_dict(raw))

    def can(self, resource: AuthorizableResource, permission: str) -> bool:
        return resource.check(self, permission)

    def get_accessible(
        self, resource_type: str, permission: str, limit: int | None = 100
    ) -> list[Obj]:
        engine = get_authorization_engine()
        return engine.list_resources(
            subject=self.get_subject_ref(),
            permission=permission,
            resource_type=resource_type,
            limit=limit or None,
        )

    def get_relations(self, resource: AuthorizableResource) -> set[str]:
        engine = get_authorization_engine()
        tuples: Iterable = engine.read_tuples(
            subject=self.get_subject_ref(), resource=resource.get_resource_ref()
        )
        return {str(t.relation) for t in tuples}


class AuthorizableGroup(AuthorizableSubject, AuthorizableResource):
    """Mixin for groups that are both subjects and resources (have members)."""

    def add_member(self, member: AuthorizableSubject) -> None:
        self.grant(member, "member")

    def remove_member(self, member: AuthorizableSubject) -> None:
        self.revoke(member, "member")

    def is_member(self, subject: AuthorizableSubject) -> bool:
        return self.check(subject, "member")

    def get_members(self) -> list[Subject]:
        return self.who_can("member")


def _normalize_to_subject(value: object) -> Subject:
    """Convert a subject-like or resource-like input into a Subject value.

    Supports:
    - AuthorizableSubject (uses get_subject_ref)
    - AuthorizableResource (uses get_resource_ref as direct subject ns:id)
    - Subject (passthrough)
    - Obj (converts to direct subject ns:id)
    - str (parsed as Subject string form)
    """

    from zanzipy.models.object import Obj as _Obj
    from zanzipy.models.subject import Subject as _Subject

    if isinstance(value, AuthorizableSubject):
        return value.get_subject_ref()
    if isinstance(value, AuthorizableResource):
        obj = value.get_resource_ref()
        return _Subject(namespace=obj.namespace, id=obj.id)
    if isinstance(value, _Subject):
        return value
    if isinstance(value, _Obj):
        return _Subject(namespace=value.namespace, id=value.id)
    if isinstance(value, str):
        return _Subject.from_string(value)
    raise TypeError(
        "subject must be AuthorizableSubject, AuthorizableResource, Subject, "
        "Obj, or str"
    )


def _coerce_obj_dict(raw: dict) -> dict:
    """Coerce object dict fields to strings as expected by Obj.from_dict."""

    ns = str(raw.get("namespace"))
    oid = str(raw.get("id"))
    return {"namespace": ns, "id": oid}


def _coerce_subject_dict(raw: dict) -> dict:
    """Coerce subject dict fields to strings as expected by Subject.from_dict."""

    ns = str(raw.get("namespace"))
    sid = str(raw.get("id"))
    rel = raw.get("relation")
    rel_s = None if rel is None else str(rel)
    return {"namespace": ns, "id": sid, "relation": rel_s}


__all__ = [
    "AuthorizableGroup",
    "AuthorizableResource",
    "AuthorizableSubject",
]
