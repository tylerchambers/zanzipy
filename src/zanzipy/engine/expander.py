from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from zanzipy.engine.rewrite_dispatch import RewriteRuleDispatcher
from zanzipy.models import EntityId, NamespaceId, Obj, Relation, Subject, TupleFilter

if TYPE_CHECKING:
    from zanzipy.schema.compiled import CompiledAuthorizationModel
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
    from zanzipy.storage.repos.abstract.relations import RelationRepository
    from zanzipy.storage.revision import ReadContext


@dataclass(frozen=True, slots=True)
class SubjectSet:
    """Result of expanding a relation: aggregated subjects.

    - users: direct user subjects in canonical string form (e.g., "user:alice")
    - usersets: subject-set anchors or non-user direct subjects
    - wildcard_exclusions: namespace wildcards with explicit finite exceptions
    """

    users: set[str] = field(default_factory=set)
    usersets: set[str] = field(default_factory=set)
    wildcard_exclusions: dict[str, set[str]] = field(default_factory=dict)

    def union(self, other: SubjectSet) -> SubjectSet:
        """Return the semantic union of two expanded subject sets."""
        return (
            _ExpandedSubjects.from_subject_set(self)
            .union(_ExpandedSubjects.from_subject_set(other))
            .to_subject_set()
        )

    def intersection(self, other: SubjectSet) -> SubjectSet:
        """Return the semantic intersection of two expanded subject sets."""
        return (
            _ExpandedSubjects.from_subject_set(self)
            .intersection(_ExpandedSubjects.from_subject_set(other))
            .to_subject_set()
        )

    def difference(self, other: SubjectSet) -> SubjectSet:
        """Return the semantic difference of two expanded subject sets."""
        return (
            _ExpandedSubjects.from_subject_set(self)
            .difference(_ExpandedSubjects.from_subject_set(other))
            .to_subject_set()
        )


@dataclass(frozen=True, slots=True)
class _ExpandedSubjects:
    """Materialized subjects with namespace-wildcard set semantics."""

    finite: set[str] = field(default_factory=set)
    wildcards: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def from_subject_set(cls, subject_set: SubjectSet) -> _ExpandedSubjects:
        return cls.from_rendered_subjects(
            subject_set.users | subject_set.usersets,
            subject_set.wildcard_exclusions,
        )

    @classmethod
    def from_rendered_subjects(
        cls,
        subjects: set[str],
        wildcard_exclusions: dict[str, set[str]] | None = None,
    ) -> _ExpandedSubjects:
        finite: set[str] = set()
        wildcards: dict[str, set[str]] = {}
        for rendered in subjects:
            namespace = _wildcard_namespace(rendered)
            if namespace is None:
                finite.add(rendered)
            else:
                wildcards.setdefault(namespace, set())

        for rendered, exclusions in (wildcard_exclusions or {}).items():
            namespace = _wildcard_namespace(rendered)
            if namespace is not None:
                wildcards.setdefault(namespace, set()).update(exclusions)

        return cls(finite=finite, wildcards=wildcards)._normalized()

    def union(self, other: _ExpandedSubjects) -> _ExpandedSubjects:
        finite = self.finite | other.finite
        wildcards: dict[str, set[str]] = {}
        for namespace in self.wildcards.keys() | other.wildcards.keys():
            left_exclusions = self.wildcards.get(namespace)
            right_exclusions = other.wildcards.get(namespace)
            if left_exclusions is not None and right_exclusions is not None:
                wildcards[namespace] = left_exclusions & right_exclusions
            elif left_exclusions is not None:
                wildcards[namespace] = left_exclusions - _subjects_in_namespace(
                    other.finite,
                    namespace,
                )
            elif right_exclusions is not None:
                wildcards[namespace] = right_exclusions - _subjects_in_namespace(
                    self.finite,
                    namespace,
                )

        return _ExpandedSubjects(finite=finite, wildcards=wildcards)._normalized()

    def intersection(self, other: _ExpandedSubjects) -> _ExpandedSubjects:
        finite = {
            rendered for rendered in self.finite if other.contains_concrete(rendered)
        } | {rendered for rendered in other.finite if self.contains_concrete(rendered)}
        wildcards = {
            namespace: self.wildcards[namespace] | other.wildcards[namespace]
            for namespace in self.wildcards.keys() & other.wildcards.keys()
        }
        return _ExpandedSubjects(finite=finite, wildcards=wildcards)._normalized()

    def difference(self, other: _ExpandedSubjects) -> _ExpandedSubjects:
        finite = {
            rendered
            for rendered in self.finite
            if not other.contains_concrete(rendered)
        }
        wildcards: dict[str, set[str]] = {}
        for namespace, exclusions in self.wildcards.items():
            other_exclusions = other.wildcards.get(namespace)
            if other_exclusions is None:
                wildcards[namespace] = exclusions | _subjects_in_namespace(
                    other.finite,
                    namespace,
                )
            else:
                finite.update(
                    rendered
                    for rendered in other_exclusions - exclusions
                    if _concrete_subject_namespace(rendered) == namespace
                )

        return _ExpandedSubjects(finite=finite, wildcards=wildcards)._normalized()

    def contains_concrete(self, rendered: str) -> bool:
        if rendered in self.finite:
            return True
        namespace = _concrete_subject_namespace(rendered)
        return (
            namespace is not None
            and namespace in self.wildcards
            and rendered not in self.wildcards[namespace]
        )

    def to_subject_set(self) -> SubjectSet:
        users: set[str] = set()
        usersets: set[str] = set()
        wildcard_exclusions: dict[str, set[str]] = {}

        for rendered in self.finite:
            _add_rendered_subject(rendered, users=users, usersets=usersets)

        for namespace, exclusions in self.wildcards.items():
            rendered = f"{namespace}:*"
            if exclusions:
                wildcard_exclusions[rendered] = set(exclusions)
            else:
                _add_rendered_subject(rendered, users=users, usersets=usersets)

        return SubjectSet(
            users=users,
            usersets=usersets,
            wildcard_exclusions=wildcard_exclusions,
        )

    def _normalized(self) -> _ExpandedSubjects:
        finite = set(self.finite)
        wildcards = {
            namespace: {
                rendered
                for rendered in exclusions
                if _concrete_subject_namespace(rendered) == namespace
            }
            for namespace, exclusions in self.wildcards.items()
        }
        for namespace, exclusions in wildcards.items():
            exclusions.difference_update(_subjects_in_namespace(finite, namespace))

        finite = {
            rendered
            for rendered in finite
            if not _wildcard_includes(wildcards, rendered)
        }
        return _ExpandedSubjects(finite=finite, wildcards=wildcards)


def _wildcard_namespace(rendered: str) -> str | None:
    subject = Subject.from_string(rendered)
    if subject.relation is None and str(subject.id) == "*":
        return str(subject.namespace)
    return None


def _concrete_subject_namespace(rendered: str) -> str | None:
    subject = Subject.from_string(rendered)
    if subject.relation is None and str(subject.id) != "*":
        return str(subject.namespace)
    return None


def _subjects_in_namespace(subjects: set[str], namespace: str) -> set[str]:
    return {
        rendered
        for rendered in subjects
        if _concrete_subject_namespace(rendered) == namespace
    }


def _wildcard_includes(
    wildcards: dict[str, set[str]],
    rendered: str,
) -> bool:
    namespace = _concrete_subject_namespace(rendered)
    return (
        namespace is not None
        and namespace in wildcards
        and rendered not in wildcards[namespace]
    )


def _add_rendered_subject(
    rendered: str,
    *,
    users: set[str],
    usersets: set[str],
) -> None:
    subject = Subject.from_string(rendered)
    if subject.relation is None and str(subject.namespace) == "user":
        users.add(rendered)
    else:
        usersets.add(rendered)


class _ExpansionIncomplete(Exception):
    """Raised internally when a strict expansion branch is truncated."""


class ExpansionEngine(RewriteRuleDispatcher):
    """Expands a relation/permission into the set of subjects that grant it.

    Direct and union expansion preserve subject-set anchors. Intersection and
    exclusion materialize those anchors before applying set algebra so flat
    results stay consistent with check semantics.
    """

    def __init__(
        self,
        *,
        relations_repository: RelationRepository,
        authorization_model: CompiledAuthorizationModel,
        max_depth: int = 25,
    ) -> None:
        """Create an expansion engine over a repository and compiled model."""
        self._relations = relations_repository
        self._max_depth = max_depth
        self._model = authorization_model

    def expand(
        self,
        object_type: str,
        object_id: str,
        relation: str,
        *,
        context: ReadContext,
    ) -> SubjectSet:
        """Return subjects that grant a relation or permission on one object.

        Inputs are validated against value-object rules and the compiled model.
        Expansion reads only from the supplied tenant revision context.
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
        fail_on_incomplete: bool = False,
    ) -> SubjectSet:
        """Expand one relation while treating ``visited`` as an active stack."""
        key = (object_type, object_id, relation)
        if key in visited or depth > self._max_depth:
            if fail_on_incomplete:
                raise _ExpansionIncomplete
            return SubjectSet()

        visited.add(key)
        try:
            rewrite = self._model.resolve(object_type, relation)
            return self._evaluate_rule(
                rewrite=rewrite,
                object_type=object_type,
                object_id=object_id,
                context=context,
                depth=depth,
                visited=visited,
                current_relation=relation,
                fail_on_incomplete=fail_on_incomplete,
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
        fail_on_incomplete: bool,
    ) -> SubjectSet:
        """Evaluate one rewrite rule against the current object."""

        return self._dispatch_rewrite_rule(
            rewrite,
            direct=self._evaluate_direct_or_this_rule,
            this=self._evaluate_direct_or_this_rule,
            computed_userset=self._evaluate_computed_userset_rule,
            tuple_to_userset=self._evaluate_tuple_to_userset_rule,
            union=self._evaluate_union_rule,
            intersection=self._evaluate_intersection_rule,
            exclusion=self._evaluate_exclusion_rule,
            object_type=object_type,
            object_id=object_id,
            context=context,
            depth=depth,
            visited=visited,
            current_relation=current_relation,
            fail_on_incomplete=fail_on_incomplete,
        )

    def _evaluate_direct_or_this_rule(
        self,
        _rewrite: DirectRule | ThisRule,
        *,
        object_type: str,
        object_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
        current_relation: str,
        fail_on_incomplete: bool,
    ) -> SubjectSet:
        return self._expand_direct(
            object_type=object_type,
            object_id=object_id,
            context=context,
            effective_relation=current_relation,
        )

    def _evaluate_computed_userset_rule(
        self,
        rewrite: ComputedUsersetRule,
        *,
        object_type: str,
        object_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
        current_relation: str,
        fail_on_incomplete: bool,
    ) -> SubjectSet:
        return self._expand_recursive(
            object_type=object_type,
            object_id=object_id,
            relation=rewrite.relation,
            context=context,
            depth=depth + 1,
            visited=visited,
            fail_on_incomplete=fail_on_incomplete,
        )

    def _evaluate_tuple_to_userset_rule(
        self,
        rewrite: TupleToUsersetRule,
        *,
        object_type: str,
        object_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
        current_relation: str,
        fail_on_incomplete: bool,
    ) -> SubjectSet:
        return self._expand_tuple_to_userset(
            object_type=object_type,
            object_id=object_id,
            tuple_relation=rewrite.tuple_relation,
            computed_relation=rewrite.computed_relation,
            context=context,
            depth=depth,
            visited=visited,
            fail_on_incomplete=fail_on_incomplete,
        )

    def _evaluate_union_rule(
        self,
        rewrite: UnionRule,
        *,
        object_type: str,
        object_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
        current_relation: str,
        fail_on_incomplete: bool,
    ) -> SubjectSet:
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
                fail_on_incomplete=fail_on_incomplete,
            )
            result = result.union(child_set)
        return result

    def _evaluate_intersection_rule(
        self,
        rewrite: IntersectionRule,
        *,
        object_type: str,
        object_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
        current_relation: str,
        fail_on_incomplete: bool,
    ) -> SubjectSet:
        accum: _ExpandedSubjects | None = None
        for child in rewrite.children:
            child_set = self._evaluate_rule(
                rewrite=child,
                object_type=object_type,
                object_id=object_id,
                context=context,
                depth=depth + 1,
                visited=visited,
                current_relation=current_relation,
                fail_on_incomplete=fail_on_incomplete,
            )
            child_subjects = self._materialize(
                child_set,
                context=context,
                depth=depth + 1,
                visited=visited,
                fail_on_incomplete=fail_on_incomplete,
            )
            accum = (
                child_subjects if accum is None else accum.intersection(child_subjects)
            )
        return SubjectSet() if accum is None else accum.to_subject_set()

    def _evaluate_exclusion_rule(
        self,
        rewrite: ExclusionRule,
        *,
        object_type: str,
        object_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
        current_relation: str,
        fail_on_incomplete: bool,
    ) -> SubjectSet:
        base = self._evaluate_rule(
            rewrite=rewrite.base,
            object_type=object_type,
            object_id=object_id,
            context=context,
            depth=depth + 1,
            visited=visited,
            current_relation=current_relation,
            fail_on_incomplete=fail_on_incomplete,
        )
        try:
            subtract = self._evaluate_rule(
                rewrite=rewrite.subtract,
                object_type=object_type,
                object_id=object_id,
                context=context,
                depth=depth + 1,
                visited=visited,
                current_relation=current_relation,
                fail_on_incomplete=True,
            )
            base_subjects = self._materialize(
                base,
                context=context,
                depth=depth + 1,
                visited=visited,
                fail_on_incomplete=fail_on_incomplete,
            )
            subtract_subjects = self._materialize(
                subtract,
                context=context,
                depth=depth + 1,
                visited=visited,
                fail_on_incomplete=True,
            )
        except _ExpansionIncomplete:
            if fail_on_incomplete:
                raise
            return SubjectSet()
        return base_subjects.difference(subtract_subjects).to_subject_set()

    def _materialize(
        self,
        subject_set: SubjectSet,
        *,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
        fail_on_incomplete: bool,
    ) -> _ExpandedSubjects:
        """Resolve userset anchors into semantic rendered subject sets."""
        subjects = _ExpandedSubjects.from_rendered_subjects(
            set(subject_set.users),
            subject_set.wildcard_exclusions,
        )
        for rendered in subject_set.usersets:
            subject = Subject.from_string(rendered)
            if subject.relation is None:
                subjects = subjects.union(
                    _ExpandedSubjects.from_rendered_subjects({rendered})
                )
                continue

            expanded = self._expand_recursive(
                object_type=str(subject.namespace),
                object_id=str(subject.id),
                relation=str(subject.relation),
                context=context,
                depth=depth + 1,
                visited=visited,
                fail_on_incomplete=fail_on_incomplete,
            )
            subjects = subjects.union(
                self._materialize(
                    expanded,
                    context=context,
                    depth=depth + 1,
                    visited=visited,
                    fail_on_incomplete=fail_on_incomplete,
                )
            )
        return subjects

    @staticmethod
    def _subject_set_from_subjects(subjects: set[str]) -> SubjectSet:
        """Split rendered subjects back into user and userset buckets."""
        return _ExpandedSubjects.from_rendered_subjects(subjects).to_subject_set()

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
            if not self._model.allows_stored_subject(
                resource_type=object_type,
                relation=effective_relation,
                subject=t.subject,
            ):
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
        fail_on_incomplete: bool,
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
            if not self._model.allows_tuple_to_userset_stored_subject(
                resource_type=object_type,
                tuple_relation=tuple_relation,
                subject=t.subject,
            ):
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
                fail_on_incomplete=fail_on_incomplete,
            )
            result = result.union(child)
        return result
