from typing import TYPE_CHECKING

from .engine.checker import CheckEngine
from .engine.expander import ExpansionEngine, SubjectSet
from .models.check import CheckRequest, CheckResponse
from .models.filter import TupleFilter
from .models.namespace import NamespaceId
from .models.object import Obj
from .models.subject import Subject
from .models.tuple import RelationTuple
from .schema.subjects import SubjectReference

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .schema.registry import SchemaRegistry
    from .storage.cache.abstract.tuples import TupleCache
    from .storage.repos.abstract.relations import RelationRepository


class ZanzibarClient:
    """
    High-level, pythonic API over Zanzibar-style authorization primitives.

    Responsibilities:
    - Provide ergonomic helpers for writes, deletes, checks, and queries
    - Validate operations against the registered schema
    - Delegate evaluation to the CheckEngine (rules-aware)

    Typical usage:
        relations_repo = InMemoryRelationRepository()
        registry = SchemaRegistry()
        client = ZanzibarClient(
            relations_repository=relations_repo,
            schema=registry,
        )

        client.write("document:readme", "owner", "user:alice")
        allowed = client.check("document:readme", "can_view", "user:alice")
        docs = client.list_objects("document", "can_view", "user:alice")
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
        # Optionally wrap the relations repository with a cache decorator
        if tuple_cache is not None:
            from .storage.repos.decorators.cached_relations import (
                CachedRelationRepository,
            )

            relations_repository = CachedRelationRepository(
                backend=relations_repository, cache=tuple_cache
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

    def write(self, object: str, relation: str, subject: str) -> None:
        """
        Grant a relation tuple.

        Args:
            object: "type:id"
            relation: relation name
            subject: "type:id" or "type:id#relation"
        """

        tuple_str = f"{object}#{relation}@{subject}"
        rt = RelationTuple.from_string(tuple_str)
        self._validate_tuple_against_schema(rt)
        self.relations_repository.write(rt)

    def write_many(self, tuples: Sequence[tuple[str, str, str]]) -> None:
        """Bulk grant: sequence of (object, relation, subject)."""

        parsed: list[RelationTuple] = []
        for obj, rel, subj in tuples:
            rt = RelationTuple.from_string(f"{obj}#{rel}@{subj}")
            self._validate_tuple_against_schema(rt)
            parsed.append(rt)
        self.relations_repository.write_many(parsed)

    def delete(self, object: str, relation: str, subject: str) -> bool:
        """Revoke a relation tuple. Returns True if a record was deleted."""

        rt = RelationTuple.from_string(f"{object}#{relation}@{subject}")
        # Allow deletes to proceed even if schema changed later; skip validation
        return self.relations_repository.delete(rt)

    def check(self, object: str, relation: str, subject: str) -> bool:
        """
        Check if a direct subject has relation/permission on an object.

        The subject must be direct (no '#'); for subject-set anchors, expand to
        a principal before calling.
        """

        request = CheckRequest.from_strings(object, relation, subject)
        response = self._check_engine.check(request)
        return response.allowed

    def check_detailed(self, object: str, relation: str, subject: str) -> CheckResponse:
        """Check and return full debugging info (trace, counters)."""

        request = CheckRequest.from_strings(object, relation, subject)
        return self._check_engine.check(request)

    def list_objects(self, object_type: str, relation: str, subject: str) -> list[str]:
        """
        Enumerate objects of a type for which a direct subject is authorized.

        This consults the rules via the check engine for correctness:
        - Candidates are discovered from existing tuples in the object namespace
        - Each candidate object is verified using the full rules evaluation
        """

        # Validate inputs using existing value objects
        # Validate namespace identifier; avoids coupling to object id rules here
        NamespaceId(object_type)
        subj = Subject.from_string(subject)
        if subj.relation is not None:
            raise ValueError("list_objects requires a direct subject (no '#relation')")

        # Discover candidate object ids from tuples in this namespace
        candidates: set[str] = set()
        for t in self.relations_repository.find(TupleFilter(object_type=object_type)):
            candidates.add(str(t.object.id))

        # Verify each candidate using a full check (rules-aware)
        results: list[str] = []
        for object_id in sorted(candidates):
            obj_str = f"{object_type}:{object_id}"
            if self.check(obj_str, relation, subject):
                results.append(obj_str)
        return results

    def list_subjects_direct(self, object: str, relation: str) -> list[str]:
        """
        List direct subjects for an object's relation (no rewrite expansion).

        Returns strings in canonical form: 'ns:id' or 'ns:id#rel'.
        """

        obj = Obj.from_string(object)
        direct = []
        for t in self.relations_repository.read(
            TupleFilter(
                object_type=str(obj.namespace),
                object_id=str(obj.id),
                relation=relation,
            )
        ):
            direct.append(str(t.subject))
        return direct

    def expand(self, object: str, relation: str) -> SubjectSet:
        """
        Expand a relation/permission into the set of subjects that grant it.

        Returns a SubjectSet with two buckets:
        - users: direct principals in 'user:...' form
        - usersets: subject-set anchors like 'group:eng#member' and also
          non-'user' direct principals in 'ns:id' form
        """

        obj = Obj.from_string(object)
        return self._expansion_engine.expand(
            object_type=str(obj.namespace),
            object_id=str(obj.id),
            relation=relation,
        )

    def ping(self) -> bool:
        """Lightweight health check across dependencies."""

        return bool(self.relations_repository.ping())

    def close(self) -> None:
        """Close underlying repositories (best-effort)."""

        self.relations_repository.close()

    def _validate_tuple_against_schema(self, rt: RelationTuple) -> None:
        """
        Ensure a tuple write conforms to schema:
        - Object namespace exists
        - Relation exists and is a relation (not a permission)
        - Subject type (and optional relation) is allowed
        """

        object_ns = str(rt.object.namespace)
        relation_name = str(rt.relation)

        rel_def = self.schema.get_relation_definition(object_ns, relation_name)
        def_type = rel_def.get("type")
        if def_type != "relation":
            raise ValueError(
                f"Cannot write to permission '{object_ns}:{relation_name}'"
            )

        allowed_subject_dicts: Iterable[dict] = rel_def.get("allowed_subjects", [])
        allowed_subjects = tuple(
            SubjectReference.from_dict(s) for s in allowed_subject_dicts
        )

        subj_ns = str(rt.subject.namespace)
        subj_rel = None if rt.subject.relation is None else str(rt.subject.relation)

        if not self._subject_is_allowed(subj_ns, subj_rel, allowed_subjects):
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
        subj_relation: str | None,
        allowed: Sequence[SubjectReference],
    ) -> bool:
        for ref in allowed:
            if ref.relation is None:
                # Allows direct subjects of the namespace (wildcard or not)
                if subj_relation is None and ref.namespace.value == subj_namespace:
                    return True
            else:
                # Allows subject sets with a specific relation
                if (
                    subj_relation is not None
                    and ref.namespace.value == subj_namespace
                    and ref.relation.value == subj_relation
                ):
                    return True
        return False
