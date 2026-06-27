from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from zanzipy.engine.resolver import RuleResolver
from zanzipy.models import EntityId, NamespaceId, Obj, Relation, Subject, TupleFilter
from zanzipy.schema.rules import (
    ComputedUsersetRule,
    DirectRule,
    ExclusionRule,
    IntersectionRule,
    RewriteRule,
    ThisRule,
    TupleToUsersetRule,
    UnionRule,
)

if TYPE_CHECKING:
    from zanzipy.schema.registry import SchemaRegistry
    from zanzipy.storage.cache.abstract.rules import CompiledRuleCache
    from zanzipy.storage.repos.abstract.relations import RelationRepository
    from zanzipy.storage.revision import ReadContext


@dataclass(frozen=True, slots=True)
class SubjectSet:
    """Result of expanding a relation: aggregated subjects.

    - users: direct user subjects in canonical string form (e.g., "user:alice")
    - usersets: subject-set anchors or non-user direct subjects
    """

    users: set[str] = field(default_factory=set)
    usersets: set[str] = field(default_factory=set)

    def union(self, other: SubjectSet) -> SubjectSet:
        """Return the bucket-wise union of two expanded subject sets."""
        return SubjectSet(
            users=set(self.users | other.users),
            usersets=set(self.usersets | other.usersets),
        )

    def intersection(self, other: SubjectSet) -> SubjectSet:
        """Return the bucket-wise intersection of two expanded subject sets."""
        return SubjectSet(
            users=set(self.users & other.users),
            usersets=set(self.usersets & other.usersets),
        )

    def difference(self, other: SubjectSet) -> SubjectSet:
        """Return the bucket-wise difference of two expanded subject sets."""
        return SubjectSet(
            users=set(self.users - other.users),
            usersets=set(self.usersets - other.usersets),
        )


class ExpansionEngine:
    """Expands a relation/permission into the set of subjects that grant it.

    Direct and union expansion preserve subject-set anchors. Intersection and
    exclusion materialize those anchors before applying set algebra so flat
    results stay consistent with check semantics.
    """

    def __init__(
        self,
        *,
        relations_repository: RelationRepository,
        schema: SchemaRegistry,
        max_depth: int = 25,
        compiled_rules_cache: CompiledRuleCache[RewriteRule] | None = None,
    ) -> None:
        """Create an expansion engine over a relation repository and schema."""
        self._relations = relations_repository
        self._max_depth = max_depth
        self._resolver = RuleResolver(
            schema=schema,
            compiled_rules_cache=compiled_rules_cache,
        )

    def expand(
        self,
        object_type: str,
        object_id: str,
        relation: str,
        *,
        context: ReadContext,
    ) -> SubjectSet:
        """Return subjects that grant a relation or permission on one object.

        Inputs are validated against the value-object rules and schema. Expansion
        reads only from the supplied tenant revision context.
        """

        # Validate identifiers
        NamespaceId(object_type)
        EntityId(object_id)
        Relation(relation)

        visited: set[tuple[str, str, str]] = set()
        return self._expand_recursive(
            object_type=object_type,
            object_id=object_id,
            relation=relation,
            context=context,
            depth=0,
            visited=visited,
        )

    def _expand_recursive(
        self,
        *,
        object_type: str,
        object_id: str,
        relation: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
    ) -> SubjectSet:
        """Expand one relation while treating ``visited`` as an active stack."""
        key = (object_type, object_id, relation)
        if key in visited or depth > self._max_depth:
            return SubjectSet()

        visited.add(key)
        try:
            rewrite = self._resolver.resolve(object_type, relation)
            return self._evaluate_rule(
                rewrite=rewrite,
                object_type=object_type,
                object_id=object_id,
                context=context,
                depth=depth,
                visited=visited,
                current_relation=relation,
            )
        finally:
            visited.remove(key)

    def _evaluate_rule(
        self,
        *,
        rewrite: RewriteRule,
        object_type: str,
        object_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
        current_relation: str,
    ) -> SubjectSet:
        """Evaluate one rewrite rule against the current object."""
        if isinstance(rewrite, (DirectRule, ThisRule)):
            return self._expand_direct(
                object_type=object_type,
                object_id=object_id,
                context=context,
                effective_relation=current_relation,
            )

        if isinstance(rewrite, ComputedUsersetRule):
            return self._expand_recursive(
                object_type=object_type,
                object_id=object_id,
                relation=rewrite.relation,
                context=context,
                depth=depth + 1,
                visited=visited,
            )

        if isinstance(rewrite, TupleToUsersetRule):
            return self._expand_tuple_to_userset(
                object_type=object_type,
                object_id=object_id,
                tuple_relation=rewrite.tuple_relation,
                computed_relation=rewrite.computed_relation,
                context=context,
                depth=depth,
                visited=visited,
            )

        if isinstance(rewrite, UnionRule):
            result = SubjectSet()
            for child in rewrite.children:
                child_set = self._evaluate_rule(
                    rewrite=child,
                    object_type=object_type,
                    object_id=object_id,
                    context=context,
                    depth=depth + 1,
                    visited=visited,
                    current_relation=current_relation,
                )
                result = result.union(child_set)
            return result

        if isinstance(rewrite, IntersectionRule):
            accum: set[str] | None = None
            for child in rewrite.children:
                child_set = self._evaluate_rule(
                    rewrite=child,
                    object_type=object_type,
                    object_id=object_id,
                    context=context,
                    depth=depth + 1,
                    visited=visited,
                    current_relation=current_relation,
                )
                child_subjects = self._materialize(
                    child_set,
                    context=context,
                    depth=depth + 1,
                    visited=visited,
                )
                accum = (
                    child_subjects
                    if accum is None
                    else accum.intersection(child_subjects)
                )
            return self._subject_set_from_subjects(accum or set())

        if isinstance(rewrite, ExclusionRule):
            base = self._evaluate_rule(
                rewrite=rewrite.base,
                object_type=object_type,
                object_id=object_id,
                context=context,
                depth=depth + 1,
                visited=visited,
                current_relation=current_relation,
            )
            subtract = self._evaluate_rule(
                rewrite=rewrite.subtract,
                object_type=object_type,
                object_id=object_id,
                context=context,
                depth=depth + 1,
                visited=visited,
                current_relation=current_relation,
            )
            base_subjects = self._materialize(
                base,
                context=context,
                depth=depth + 1,
                visited=visited,
            )
            subtract_subjects = self._materialize(
                subtract,
                context=context,
                depth=depth + 1,
                visited=visited,
            )
            return self._subject_set_from_subjects(base_subjects - subtract_subjects)

        # Unknown node
        return SubjectSet()

    def _materialize(
        self,
        subject_set: SubjectSet,
        *,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
    ) -> set[str]:
        """Resolve userset anchors into concrete rendered subjects."""
        subjects = set(subject_set.users)
        for rendered in subject_set.usersets:
            subject = Subject.from_string(rendered)
            if subject.relation is None:
                subjects.add(rendered)
                continue

            expanded = self._expand_recursive(
                object_type=str(subject.namespace),
                object_id=str(subject.id),
                relation=str(subject.relation),
                context=context,
                depth=depth + 1,
                visited=visited,
            )
            subjects.update(
                self._materialize(
                    expanded,
                    context=context,
                    depth=depth + 1,
                    visited=visited,
                )
            )
        return subjects

    @staticmethod
    def _subject_set_from_subjects(subjects: set[str]) -> SubjectSet:
        """Split rendered subjects back into user and userset buckets."""
        users: set[str] = set()
        usersets: set[str] = set()
        for rendered in subjects:
            subject = Subject.from_string(rendered)
            if subject.relation is None and str(subject.namespace) == "user":
                users.add(rendered)
            else:
                usersets.add(rendered)
        return SubjectSet(users=users, usersets=usersets)

    def _expand_direct(
        self,
        *,
        object_type: str,
        object_id: str,
        context: ReadContext,
        effective_relation: str,
    ) -> SubjectSet:
        """Collect direct tuples for the effective relation on one object."""
        obj = Obj.from_parts(object_type, object_id)
        users: set[str] = set()
        usersets: set[str] = set()
        for t in self._relations.read(TupleFilter.from_object(obj), context=context):
            if str(t.relation) != effective_relation:
                continue
            # Subject set anchor
            if t.subject.relation is not None:
                usersets.add(str(t.subject))
                continue
            # Direct subject; capture "user:" explicitly, but include others too
            if str(t.subject.namespace) == "user":
                users.add(str(t.subject))
            else:
                # Non-user principals appear as direct subjects; represent as
                # userset-like
                usersets.add(str(t.subject))
        return SubjectSet(users=users, usersets=usersets)

    def _expand_tuple_to_userset(
        self,
        *,
        object_type: str,
        object_id: str,
        tuple_relation: str,
        computed_relation: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
    ) -> SubjectSet:
        """Follow tuple-to-userset edges and union the target expansions."""
        obj = Obj.from_parts(object_type, object_id)
        result = SubjectSet()
        for t in self._relations.read(TupleFilter.from_object(obj), context=context):
            if str(t.relation) != tuple_relation:
                continue
            # Only follow object references (no subject-set here)
            if t.subject.relation is not None:
                continue
            next_object_type = str(t.subject.namespace)
            next_object_id = str(t.subject.id)
            child = self._expand_recursive(
                object_type=next_object_type,
                object_id=next_object_id,
                relation=computed_relation,
                context=context,
                depth=depth + 1,
                visited=visited,
            )
            result = result.union(child)
        return result
