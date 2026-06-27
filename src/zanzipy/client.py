from typing import TYPE_CHECKING

from .engine.checker import CheckEngine
from .engine.expander import ExpansionEngine, SubjectSet
from .models import (
    CheckRequest,
    CheckResponse,
    Obj,
    Relation,
    RelationTuple,
    Subject,
    TupleFilter,
)
from .schema.relations import RelationDef
from .storage.revision import (
    Consistency,
    Revision,
    TupleMutation,
    WriteResult,
    revision_for_consistency,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .schema.registry import SchemaRegistry
    from .schema.subjects import SubjectReference
    from .storage.cache.abstract.tuples import TupleCache
    from .storage.repos.abstract.relations import RelationRepository


class ZanzibarClient:
    """
    High-level, pythonic API over Zanzibar-style authorization primitives.

    All relation storage is revisioned. Convenience read APIs resolve to the
    repository head revision by default; explicit revision APIs evaluate at the
    supplied snapshot revision.
    """

    def __init__(
        self,
        *,
        relations_repository: RelationRepository,
        schema: SchemaRegistry,
        check_engine: CheckEngine | None = None,
        enable_debug: bool = False,
        max_check_depth: int = 25,
        tuple_cache: TupleCache | None = None,
    ) -> None:
        if tuple_cache is not None:
            from .storage.repos.decorators.cached_relations import (
                CachedRelationRepository,
            )

            relations_repository = CachedRelationRepository(
                backend=relations_repository,
                cache=tuple_cache,
            )

        self.relations_repository = relations_repository
        self.schema = schema
        self.enable_debug = enable_debug
        self.max_check_depth = max_check_depth

        self._check_engine = (
            check_engine
            if check_engine is not None
            else CheckEngine(
                relations_repository=relations_repository,
                schema=schema,
                max_depth=max_check_depth,
                enable_debug=enable_debug,
            )
        )
        self._expansion_engine = ExpansionEngine(
            relations_repository=relations_repository,
            schema=schema,
            max_depth=max_check_depth,
        )

    def write(self, object: str, relation: str, subject: str) -> WriteResult:
        """Grant a relation tuple and return the committed revision."""

        rt = RelationTuple.from_strings(object, relation, subject)
        self._validate_tuple_against_schema(rt)
        return self.relations_repository.write((TupleMutation.touch(rt),))

    def write_many(self, tuples: Sequence[tuple[str, str, str]]) -> WriteResult:
        """Bulk grant: sequence of (object, relation, subject)."""

        mutations: list[TupleMutation] = []
        for obj, rel, subj in tuples:
            rt = RelationTuple.from_strings(obj, rel, subj)
            self._validate_tuple_against_schema(rt)
            mutations.append(TupleMutation.touch(rt))
        return self.relations_repository.write(mutations)

    def delete(self, object: str, relation: str, subject: str) -> WriteResult:
        """Revoke a relation tuple and return the committed revision."""

        rt = RelationTuple.from_strings(object, relation, subject)
        return self.relations_repository.write((TupleMutation.delete(rt),))

    def check(
        self,
        object: str,
        relation: str,
        subject: str,
        *,
        consistency: Consistency | None = None,
    ) -> bool:
        """Check if a direct subject has relation/permission on an object."""

        revision = self._revision_for_consistency(consistency)
        return self.check_at_revision(
            object,
            relation,
            subject,
            revision=revision,
        )

    def check_at_revision(
        self,
        object: str,
        relation: str,
        subject: str,
        *,
        revision: Revision,
    ) -> bool:
        """Check at an exact relation repository revision."""

        request = CheckRequest.from_strings(object, relation, subject)
        response = self._check_engine.check(request, revision=revision)
        return response.allowed

    def check_detailed(
        self,
        object: str,
        relation: str,
        subject: str,
        *,
        consistency: Consistency | None = None,
    ) -> CheckResponse:
        """Check and return full debugging info (trace, counters)."""

        revision = self._revision_for_consistency(consistency)
        return self.check_detailed_at_revision(
            object,
            relation,
            subject,
            revision=revision,
        )

    def check_detailed_at_revision(
        self,
        object: str,
        relation: str,
        subject: str,
        *,
        revision: Revision,
    ) -> CheckResponse:
        """Check at an exact revision and return debugging info."""

        request = CheckRequest.from_strings(object, relation, subject)
        return self._check_engine.check(request, revision=revision)

    def list_objects(
        self,
        object_type: str,
        relation: str,
        subject: str,
        *,
        consistency: Consistency | None = None,
    ) -> list[str]:
        """Enumerate objects of a type for which a direct subject is authorized."""

        revision = self._revision_for_consistency(consistency)
        return self.list_objects_at_revision(
            object_type,
            relation,
            subject,
            revision=revision,
        )

    def list_objects_at_revision(
        self,
        object_type: str,
        relation: str,
        subject: str,
        *,
        revision: Revision,
    ) -> list[str]:
        """Enumerate authorized objects at an exact revision."""

        Subject.from_string(subject).require_direct()
        candidates: set[str] = set()
        for t in self.relations_repository.read(
            TupleFilter(object_type=object_type),
            revision=revision,
        ):
            candidates.add(str(t.object.id))

        results: list[str] = []
        for object_id in sorted(candidates):
            obj_str = f"{object_type}:{object_id}"
            request = CheckRequest.from_strings(obj_str, relation, subject)
            if self._check_engine.check(request, revision=revision).allowed:
                results.append(obj_str)
        return results

    def list_subjects_direct(
        self,
        object: str,
        relation: str,
        *,
        consistency: Consistency | None = None,
    ) -> list[str]:
        """List direct subjects for an object's relation at the resolved revision."""

        revision = self._revision_for_consistency(consistency)
        return self.list_subjects_direct_at_revision(
            object,
            relation,
            revision=revision,
        )

    def list_subjects_direct_at_revision(
        self,
        object: str,
        relation: str,
        *,
        revision: Revision,
    ) -> list[str]:
        """List direct subjects for an object's relation at an exact revision."""

        obj = Obj.from_string(object)
        direct = []
        for t in self.relations_repository.read(
            TupleFilter.from_parts(obj=obj, relation=Relation(relation)),
            revision=revision,
        ):
            direct.append(str(t.subject))
        return direct

    def expand(
        self,
        object: str,
        relation: str,
        *,
        consistency: Consistency | None = None,
    ) -> SubjectSet:
        """Expand a relation/permission into subjects at the resolved revision."""

        revision = self._revision_for_consistency(consistency)
        return self.expand_at_revision(object, relation, revision=revision)

    def expand_at_revision(
        self,
        object: str,
        relation: str,
        *,
        revision: Revision,
    ) -> SubjectSet:
        """Expand a relation/permission into subjects at an exact revision."""

        obj = Obj.from_string(object)
        return self._expansion_engine.expand(
            object_type=str(obj.namespace),
            object_id=str(obj.id),
            relation=relation,
            revision=revision,
        )

    def read_tuples(
        self,
        filter: TupleFilter,
        *,
        consistency: Consistency | None = None,
    ) -> Iterable[RelationTuple]:
        """Read tuples matching ``filter`` at the requested consistency."""

        revision = self._revision_for_consistency(consistency)
        return self.relations_repository.read(filter, revision=revision)

    def read_tuples_at_revision(
        self,
        filter: TupleFilter,
        *,
        revision: Revision,
    ) -> Iterable[RelationTuple]:
        """Read tuples matching ``filter`` at an exact revision."""

        return self.relations_repository.read(filter, revision=revision)

    def head_revision(self) -> Revision:
        """Return the latest relation repository revision."""

        return self.relations_repository.head_revision()

    def ping(self) -> bool:
        """Lightweight health check across dependencies."""

        return bool(self.relations_repository.ping())

    def close(self) -> None:
        """Close underlying repositories (best-effort)."""

        self.relations_repository.close()

    def _revision_for_consistency(
        self,
        consistency: Consistency | None,
    ) -> Revision:
        return revision_for_consistency(
            self.relations_repository.head_revision(),
            consistency,
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
