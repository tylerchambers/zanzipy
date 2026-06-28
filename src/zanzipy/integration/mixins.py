"""Domain mixins for resource, subject, and group integration.

These ABC-style mixins provide concrete authorization helpers while requiring
domain models to implement only minimal reference methods.
"""

from typing import TYPE_CHECKING

from zanzipy.engine_integration import get_authorization_engine
from zanzipy.storage.revision import AtExactRevision

if TYPE_CHECKING:
    from collections.abc import Iterable

    from zanzipy.engine.expander import SubjectSet
    from zanzipy.models import Obj, Subject


class AuthorizableResource:
    """Mixin for domain resources protected through the configured engine."""

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
        from zanzipy.models import Obj as _Obj

        return _Obj.from_dict(_coerce_obj_dict(raw))

    # Concrete helper methods
    def grant(self, subject: object, relation: str) -> None:
        """Grant a relation on this resource through the configured engine."""
        engine = get_authorization_engine()
        subject_ref = _normalize_to_subject(subject)
        engine.write_tuple(
            subject=subject_ref,
            relation=relation,
            resource=self.get_resource_ref(),
        )

    def revoke(self, subject: object, relation: str) -> None:
        """Revoke a relation on this resource through the configured engine."""
        engine = get_authorization_engine()
        subject_ref = _normalize_to_subject(subject)
        engine.delete_tuple(
            subject=subject_ref,
            relation=relation,
            resource=self.get_resource_ref(),
        )

    def check(self, subject: AuthorizableSubject, permission: str) -> bool:
        """Return whether a subject has a permission on this resource."""
        engine = get_authorization_engine()
        return engine.check(
            subject=subject.get_subject_ref(),
            permission=permission,
            resource=self.get_resource_ref(),
        )

    def who_can(self, permission: str) -> list[Subject]:
        """Return subjects that currently satisfy a permission on this resource."""
        engine = get_authorization_engine()
        subject_set: SubjectSet = engine.expand(
            permission=permission, resource=self.get_resource_ref()
        )
        if subject_set.wildcard_exclusions:
            raise ValueError(
                "who_can cannot represent wildcard subjects with exclusions; "
                "use expand() to inspect wildcard_exclusions"
            )
        # Convert canonical strings to Subject objects
        from zanzipy.models import Subject as _Subject

        results: list[_Subject] = []
        for s in sorted(subject_set.users | subject_set.usersets):
            results.append(_Subject.from_string(s))
        return results

    def get_permissions(self, subject: AuthorizableSubject) -> set[str]:
        """Return schema permissions granted to the subject on one resource snapshot."""
        engine = get_authorization_engine()
        obj = self.get_resource_ref()
        subject_ref = subject.get_subject_ref()
        ns = engine.schema.get_namespace(str(obj.namespace))
        permission_names = tuple(ns.permissions.keys())
        consistency = AtExactRevision(engine.head_token())
        return {
            name
            for name in permission_names
            if engine.check(
                subject=subject_ref,
                permission=name,
                resource=obj,
                consistency=consistency,
            )
        }


class AuthorizableSubject:
    """Mixin for domain subjects that access resources through the engine."""

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
        from zanzipy.models import Subject as _Subject

        return _Subject.from_dict(_coerce_subject_dict(raw))

    def can(self, resource: AuthorizableResource, permission: str) -> bool:
        """Return whether this subject can access a resource permission."""
        return resource.check(self, permission)

    def get_accessible(
        self, resource_type: str, permission: str, limit: int | None = 100
    ) -> list[Obj]:
        """List resources this subject can access for a permission."""
        engine = get_authorization_engine()
        return engine.list_resources(
            subject=self.get_subject_ref(),
            permission=permission,
            resource_type=resource_type,
            limit=limit,
        )

    def get_relations(self, resource: AuthorizableResource) -> set[str]:
        """Return stored relations between this subject and a resource."""
        engine = get_authorization_engine()
        tuples: Iterable = engine.read_tuples(
            subject=self.get_subject_ref(), resource=resource.get_resource_ref()
        )
        return {str(t.relation) for t in tuples}


class AuthorizableGroup(AuthorizableSubject, AuthorizableResource):
    """Mixin for groups modeled as both member subjects and resources."""

    def add_member(self, member: AuthorizableSubject) -> None:
        """Grant group membership to a subject through the configured engine."""
        self.grant(member, "member")

    def remove_member(self, member: AuthorizableSubject) -> None:
        """Remove group membership from a subject through the configured engine."""
        self.revoke(member, "member")

    def is_member(self, subject: AuthorizableSubject) -> bool:
        """Return whether a subject is a direct or expanded group member."""
        return self.check(subject, "member")

    def get_members(self) -> list[Subject]:
        """Return subjects that satisfy this group's member relation."""
        return self.who_can("member")


def _normalize_to_subject(value: object) -> Subject:
    """Convert a subject-like or resource-like input into a Subject value.

    - AuthorizableSubject or object with get_subject_ref()
    - AuthorizableResource or object with get_resource_ref()
    - Subject (passthrough)
    - Obj (converts to direct subject ns:id)
    - str (parsed as Subject string form)
    """

    from zanzipy.models import Obj as _Obj, Subject as _Subject

    if isinstance(value, AuthorizableSubject):
        return value.get_subject_ref()
    if isinstance(value, AuthorizableResource):
        return _Subject.from_object(value.get_resource_ref())
    if isinstance(value, _Subject):
        return value
    if isinstance(value, _Obj):
        return _Subject.from_object(value)

    get_subject_ref = getattr(value, "get_subject_ref", None)
    if callable(get_subject_ref):
        subject = get_subject_ref()
        if isinstance(subject, _Subject):
            return subject
    get_resource_ref = getattr(value, "get_resource_ref", None)
    if callable(get_resource_ref):
        obj = get_resource_ref()
        if isinstance(obj, _Obj):
            return _Subject.from_object(obj)

    if isinstance(value, str):
        return _Subject.from_string(value)
    raise TypeError(
        "subject must be AuthorizableSubject, AuthorizableResource, Subject, "
        "Obj, str, or expose get_subject_ref()/get_resource_ref()"
    )


def _coerce_obj_dict(raw: dict) -> dict:
    """Coerce object dict fields to strings as expected by Obj.from_dict."""

    return {
        "namespace": _required_ref_value(raw, "namespace"),
        "id": _required_ref_value(raw, "id"),
    }


def _coerce_subject_dict(raw: dict) -> dict:
    """Coerce subject dict fields to strings as expected by Subject.from_dict."""

    rel = raw.get("relation")
    return {
        "namespace": _required_ref_value(raw, "namespace"),
        "id": _required_ref_value(raw, "id"),
        "relation": None if rel is None else str(rel),
    }


def _required_ref_value(raw: dict, key: str) -> str:
    try:
        value = raw[key]
    except KeyError as exc:
        raise ValueError(f"reference dictionary missing required key {key!r}") from exc
    if value is None:
        raise ValueError(f"reference dictionary key {key!r} cannot be None")
    return str(value)


__all__ = [
    "AuthorizableGroup",
    "AuthorizableResource",
    "AuthorizableSubject",
]
