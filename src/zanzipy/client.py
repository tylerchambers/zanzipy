from typing import TYPE_CHECKING, Self

from .engine.authorization import AuthorizationEngine
from .models import (
    CheckRequest,
    CheckResponse,
    LookupResourcesRequest,
    Obj,
    Relation,
    RelationTuple,
    TupleFilter,
)
from .schema.relations import RelationDef
from .storage.revision import (
    Consistency,
    ReadContext,
    Revision,
    RevisionToken,
    TenantId,
    TupleMutation,
    WriteContext,
    WriteResult,
    revision_for_consistency,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .engine.expander import SubjectSet
    from .schema.registry import SchemaRegistry
    from .schema.subjects import SubjectReference
    from .storage.cache.abstract.tuples import TupleCache
    from .storage.repos.abstract.relations import RelationRepository

_DEFAULT_TENANT = TenantId("default")


class ZanzibarClient:
    """
    High-level, pythonic API over Zanzibar-style authorization primitives.

    All relation storage is tenant-scoped and revisioned. Convenience read APIs
    resolve to the configured tenant's repository head revision by default;
    explicit revision APIs evaluate at the supplied tenant snapshot revision.
    """

    def __init__(
        self,
        *,
        relations_repository: RelationRepository,
        schema: SchemaRegistry,
        tenant: TenantId | str = _DEFAULT_TENANT,
        enable_debug: bool = False,
        max_check_depth: int = 25,
        tuple_cache: TupleCache | None = None,
    ) -> None:
        """Create a tenant-scoped client over a schema and relation repository."""

        self.tenant = tenant if isinstance(tenant, TenantId) else TenantId(tenant)
        self._authorization_engine = AuthorizationEngine(
            relations_repository=relations_repository,
            schema=schema,
            max_depth=max_check_depth,
            enable_debug=enable_debug,
            tuple_cache=tuple_cache,
        )

    @classmethod
    def from_authorization_engine(
        cls,
        authorization_engine: AuthorizationEngine,
        *,
        tenant: TenantId | str = _DEFAULT_TENANT,
    ) -> Self:
        """Create a tenant adapter around an existing authorization engine."""

        client = cls.__new__(cls)
        client.tenant = tenant if isinstance(tenant, TenantId) else TenantId(tenant)
        client._authorization_engine = authorization_engine
        return client

    @property
    def authorization_engine(self) -> AuthorizationEngine:
        """Return the cohesive authorization engine used by read APIs."""

        return self._authorization_engine

    @property
    def relations_repository(self) -> RelationRepository:
        """Return the repository owned by the authorization engine."""

        return self._authorization_engine.relations_repository

    @property
    def schema(self) -> SchemaRegistry:
        """Return the schema owned by the authorization engine."""

        return self._authorization_engine.schema

    @property
    def enable_debug(self) -> bool:
        """Return whether check diagnostics are enabled."""

        return self._authorization_engine.enable_debug

    @property
    def max_check_depth(self) -> int:
        """Return the authorization traversal depth limit."""

        return self._authorization_engine.max_depth

    def write(self, object: str, relation: str, subject: str) -> WriteResult:
        """Grant a relation tuple in this client's tenant."""

        rt = RelationTuple.from_strings(object, relation, subject)
        self._validate_tuple_against_schema(rt)
        return self.relations_repository.write(
            self._write_context(),
            (TupleMutation.touch(rt),),
        )

    def write_many(self, tuples: Sequence[tuple[str, str, str]]) -> WriteResult:
        """Grant multiple relation tuples in one tenant repository commit."""

        mutations: list[TupleMutation] = []
        for obj, rel, subj in tuples:
            rt = RelationTuple.from_strings(obj, rel, subj)
            self._validate_tuple_against_schema(rt)
            mutations.append(TupleMutation.touch(rt))
        return self.relations_repository.write(self._write_context(), mutations)

    def delete(self, object: str, relation: str, subject: str) -> WriteResult:
        """Revoke a relation tuple in this client's tenant."""

        rt = RelationTuple.from_strings(object, relation, subject)
        return self.relations_repository.write(
            self._write_context(),
            (TupleMutation.delete(rt),),
        )

    def check(
        self,
        object: str,
        relation: str,
        subject: str,
        *,
        consistency: Consistency | None = None,
    ) -> bool:
        """Return whether a direct subject is authorized at requested consistency."""

        context = self._context_for_consistency(consistency)
        request = CheckRequest.from_strings(object, relation, subject)
        response = self._authorization_engine.check(request, context=context)
        return response.allowed

    def check_at_revision(
        self,
        object: str,
        relation: str,
        subject: str,
        *,
        revision: Revision | RevisionToken,
    ) -> bool:
        """Return whether a direct subject is authorized at an exact tenant revision."""

        response = self.check_detailed_at_revision(
            object,
            relation,
            subject,
            revision=revision,
        )
        return response.allowed

    def check_detailed(
        self,
        object: str,
        relation: str,
        subject: str,
        *,
        consistency: Consistency | None = None,
    ) -> CheckResponse:
        """Return the full check result, including optional debug trace data."""

        context = self._context_for_consistency(consistency)
        request = CheckRequest.from_strings(object, relation, subject)
        return self._authorization_engine.check(request, context=context)

    def check_detailed_at_revision(
        self,
        object: str,
        relation: str,
        subject: str,
        *,
        revision: Revision | RevisionToken,
    ) -> CheckResponse:
        """Return the full check result evaluated against an exact tenant revision."""

        request = CheckRequest.from_strings(object, relation, subject)
        return self._authorization_engine.check(
            request,
            context=self._read_context_at_revision(revision),
        )

    def list_objects(
        self,
        object_type: str,
        relation: str,
        subject: str,
        *,
        consistency: Consistency | None = None,
    ) -> list[str]:
        """Enumerate objects of a type for which a direct subject is authorized."""

        context = self._context_for_consistency(consistency)
        return self._list_objects_in_context(
            object_type,
            relation,
            subject,
            context=context,
        )

    def list_objects_at_revision(
        self,
        object_type: str,
        relation: str,
        subject: str,
        *,
        revision: Revision | RevisionToken,
    ) -> list[str]:
        """Enumerate authorized objects at an exact tenant revision."""

        return self._list_objects_in_context(
            object_type,
            relation,
            subject,
            context=self._read_context_at_revision(revision),
        )

    def list_subjects_direct(
        self,
        object: str,
        relation: str,
        *,
        consistency: Consistency | None = None,
    ) -> list[str]:
        """List direct subjects for an object's relation at the resolved revision."""

        context = self._context_for_consistency(consistency)
        return self._list_subjects_direct_in_context(
            object,
            relation,
            context=context,
        )

    def list_subjects_direct_at_revision(
        self,
        object: str,
        relation: str,
        *,
        revision: Revision | RevisionToken,
    ) -> list[str]:
        """List direct subjects for an object's relation at an exact tenant revision."""

        return self._list_subjects_direct_in_context(
            object,
            relation,
            context=self._read_context_at_revision(revision),
        )

    def expand(
        self,
        object: str,
        relation: str,
        *,
        consistency: Consistency | None = None,
    ) -> SubjectSet:
        """Expand a relation/permission into subjects at the resolved revision."""

        context = self._context_for_consistency(consistency)
        return self._expand_in_context(object, relation, context=context)

    def expand_at_revision(
        self,
        object: str,
        relation: str,
        *,
        revision: Revision | RevisionToken,
    ) -> SubjectSet:
        """Expand a relation/permission into subjects at an exact tenant revision."""

        return self._expand_in_context(
            object,
            relation,
            context=self._read_context_at_revision(revision),
        )

    def read_tuples(
        self,
        filter: TupleFilter,
        *,
        consistency: Consistency | None = None,
    ) -> Iterable[RelationTuple]:
        """Read tuples matching ``filter`` at the requested consistency."""

        return self.relations_repository.read(
            filter,
            context=self._context_for_consistency(consistency),
        )

    def read_tuples_at_revision(
        self,
        filter: TupleFilter,
        *,
        revision: Revision | RevisionToken,
    ) -> Iterable[RelationTuple]:
        """Read tuples matching ``filter`` at an exact tenant revision."""

        return self.relations_repository.read(
            filter,
            context=self._read_context_at_revision(revision),
        )

    def head_revision(self) -> Revision:
        """Return the latest relation repository revision for this tenant."""

        return self.relations_repository.head_revision(self.tenant)

    def head_token(self) -> RevisionToken:
        """Return the tenant-scoped head revision token for this client."""

        return RevisionToken(self.tenant, self.head_revision())

    def ping(self) -> bool:
        """Lightweight health check across dependencies."""

        return bool(self.relations_repository.ping())

    def close(self) -> None:
        """Close underlying repositories (best-effort)."""

        self.relations_repository.close()

    def _write_context(self) -> WriteContext:
        return WriteContext(self.tenant)

    def _read_context_at_revision(
        self, revision: Revision | RevisionToken
    ) -> ReadContext:
        if isinstance(revision, RevisionToken):
            if revision.tenant != self.tenant:
                raise ValueError(
                    "revision token tenant does not match client tenant: "
                    f"requested {revision.tenant}, client {self.tenant}"
                )
            return ReadContext(revision.tenant, revision.revision)
        return ReadContext(self.tenant, revision)

    def _context_for_consistency(
        self,
        consistency: Consistency | None,
    ) -> ReadContext:
        token = revision_for_consistency(
            RevisionToken(self.tenant, self.head_revision()),
            consistency,
        )
        return ReadContext(token.tenant, token.revision)

    def _list_objects_in_context(
        self,
        object_type: str,
        relation: str,
        subject: str,
        *,
        context: ReadContext,
    ) -> list[str]:
        request = LookupResourcesRequest.from_strings(object_type, relation, subject)
        response = self._authorization_engine.lookup_resources(
            request,
            context=context,
        )
        return [str(resource) for resource in response.resources]

    def _list_subjects_direct_in_context(
        self,
        object: str,
        relation: str,
        *,
        context: ReadContext,
    ) -> list[str]:
        obj = Obj.from_string(object)
        direct = []
        for t in self.relations_repository.read(
            TupleFilter.from_parts(obj=obj, relation=Relation(relation)),
            context=context,
        ):
            direct.append(str(t.subject))
        return direct

    def _expand_in_context(
        self,
        object: str,
        relation: str,
        *,
        context: ReadContext,
    ) -> SubjectSet:
        obj = Obj.from_string(object)
        return self._authorization_engine.expand(
            object_type=str(obj.namespace),
            object_id=str(obj.id),
            relation=relation,
            context=context,
        )

    def _validate_tuple_against_schema(self, rt: RelationTuple) -> None:
        """Ensure a tuple write conforms to schema."""

        object_ns = str(rt.object.namespace)
        relation_name = str(rt.relation)

        definition = self.schema.get_definition(object_ns, relation_name)
        if not isinstance(definition, RelationDef):
            raise ValueError(
                f"Cannot write to permission '{object_ns}:{relation_name}'"
            )

        allowed_subjects = definition.allowed_subjects

        subj_ns = str(rt.subject.namespace)
        subj_id = str(rt.subject.id)
        subj_rel = None if rt.subject.relation is None else str(rt.subject.relation)

        if not self._subject_is_allowed(subj_ns, subj_id, subj_rel, allowed_subjects):
            rendered = []
            for s in allowed_subjects:
                rel_part = f"#{s.relation.value}" if s.relation else ""
                wildcard_part = ":*" if s.wildcard else ""
                rendered.append(f"{s.namespace.value}{rel_part}{wildcard_part}")
            raise ValueError(
                "Subject not allowed by schema: "
                f"got '{subj_ns}{('#' + subj_rel) if subj_rel else ''}', "
                f"allowed: {rendered}"
            )

    @staticmethod
    def _subject_is_allowed(
        subj_namespace: str,
        subj_id: str,
        subj_relation: str | None,
        allowed: Sequence[SubjectReference],
    ) -> bool:
        for ref in allowed:
            if ref.allows(
                namespace=subj_namespace,
                entity_id=subj_id,
                relation=subj_relation,
            ):
                return True
        return False
