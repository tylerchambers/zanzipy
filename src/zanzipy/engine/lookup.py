from typing import TYPE_CHECKING

from zanzipy.engine.checker import CheckEngine
from zanzipy.models import (
    CheckRequest,
    EntityId,
    NamespaceId,
    Obj,
    Relation,
    Subject,
    TupleFilter,
)
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
    from zanzipy.schema.compiled import CompiledAuthorizationModel
    from zanzipy.storage.repos.abstract.relations import RelationRepository
    from zanzipy.storage.revision import ReadContext


class LookupEngine:
    """Evaluates reverse LookupResources traversals from a subject to objects."""

    def __init__(
        self,
        *,
        relations_repository: RelationRepository,
        authorization_model: CompiledAuthorizationModel,
        max_depth: int = 25,
    ) -> None:
        """Create a reverse lookup engine over a repository and compiled model."""
        self._relations = relations_repository
        self._max_depth = max_depth
        self._model = authorization_model
        self._checker = CheckEngine(
            relations_repository=relations_repository,
            authorization_model=authorization_model,
            max_depth=max_depth,
        )

    def lookup_resources(
        self,
        *,
        resource_type: str,
        permission: str,
        subject: Subject,
        context: ReadContext,
    ) -> list[Obj]:
        """Return resources of ``resource_type`` that grant ``permission``."""
        NamespaceId(resource_type)
        Relation(permission)
        subject.require_direct()

        resources = self._lookup_relation(
            resource_type=resource_type,
            relation=permission,
            subject=subject,
            context=context,
            depth=0,
        )
        return sorted(resources, key=str)

    def _lookup_relation(
        self,
        *,
        resource_type: str,
        relation: str,
        subject: Subject,
        context: ReadContext,
        depth: int,
    ) -> set[Obj]:
        """Start an independent reverse relation lookup with a fresh cycle stack."""

        return self._lookup_recursive(
            resource_type=resource_type,
            relation=relation,
            subject=subject,
            context=context,
            depth=depth,
            visited=set(),
        )

    def _lookup_recursive(
        self,
        *,
        resource_type: str,
        relation: str,
        subject: Subject,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
    ) -> set[Obj]:
        if depth > self._max_depth:
            return set()

        try:
            rewrite = self._model.resolve(resource_type, relation)
        except ValueError:
            return set()

        if isinstance(rewrite, (DirectRule, ThisRule)):
            return self._evaluate_rule(
                rewrite=rewrite,
                resource_type=resource_type,
                subject=subject,
                context=context,
                depth=depth,
                current_relation=relation,
                visited=visited,
            )

        key = (resource_type, relation, str(subject))
        if key in visited:
            return set()

        visited.add(key)
        try:
            return self._evaluate_rule(
                rewrite=rewrite,
                resource_type=resource_type,
                subject=subject,
                context=context,
                depth=depth,
                current_relation=relation,
                visited=visited,
            )
        finally:
            visited.remove(key)

    def _evaluate_rule(
        self,
        *,
        rewrite: RewriteRule,
        resource_type: str,
        subject: Subject,
        context: ReadContext,
        depth: int,
        current_relation: str,
        visited: set[tuple[str, str, str]],
    ) -> set[Obj]:
        if isinstance(rewrite, (DirectRule, ThisRule)):
            return self._lookup_direct(
                resource_type=resource_type,
                relation=current_relation,
                subject=subject,
                context=context,
                depth=depth,
            )

        if isinstance(rewrite, ComputedUsersetRule):
            return self._lookup_recursive(
                resource_type=resource_type,
                relation=rewrite.relation,
                subject=subject,
                context=context,
                depth=depth + 1,
                visited=visited,
            )

        if isinstance(rewrite, TupleToUsersetRule):
            return self._lookup_tuple_to_userset(
                resource_type=resource_type,
                tuple_relation=rewrite.tuple_relation,
                computed_relation=rewrite.computed_relation,
                subject=subject,
                context=context,
                depth=depth,
            )

        if isinstance(rewrite, UnionRule):
            result: set[Obj] = set()
            for child in rewrite.children:
                result.update(
                    self._evaluate_rule(
                        rewrite=child,
                        resource_type=resource_type,
                        subject=subject,
                        context=context,
                        depth=depth + 1,
                        current_relation=current_relation,
                        visited=visited,
                    )
                )
            return result

        if isinstance(rewrite, IntersectionRule):
            result: set[Obj] | None = None
            for child in rewrite.children:
                child_result = self._evaluate_rule(
                    rewrite=child,
                    resource_type=resource_type,
                    subject=subject,
                    context=context,
                    depth=depth + 1,
                    current_relation=current_relation,
                    visited=visited,
                )
                result = child_result if result is None else result & child_result
                if not result:
                    return set()
            return result or set()

        if isinstance(rewrite, ExclusionRule):
            base = self._evaluate_rule(
                rewrite=rewrite.base,
                resource_type=resource_type,
                subject=subject,
                context=context,
                depth=depth + 1,
                current_relation=current_relation,
                visited=visited,
            )
            if not base:
                return set()
            subtract = self._evaluate_rule(
                rewrite=rewrite.subtract,
                resource_type=resource_type,
                subject=subject,
                context=context,
                depth=depth + 1,
                current_relation=current_relation,
                visited=visited,
            )
            return base - subtract

        return set()

    def _lookup_direct(
        self,
        *,
        resource_type: str,
        relation: str,
        subject: Subject,
        context: ReadContext,
        depth: int,
    ) -> set[Obj]:
        """Walk reverse subject edges until all reachable direct grants are known."""

        resources: set[Obj] = set()
        reachable_usersets = self._reachable_userset_refs(
            resource_type=resource_type,
            relation=relation,
        )
        semantic_parents = self._semantic_parent_refs(reachable_usersets)
        worklist = [
            (direct_subject, depth)
            for direct_subject in self._direct_subject_matches(subject)
        ]
        seen_depths = dict(worklist)
        next_subject = 0

        while next_subject < len(worklist):
            current_subject, current_depth = worklist[next_subject]
            next_subject += 1
            if current_depth > self._max_depth:
                continue

            current_ref = (
                (str(current_subject.namespace), str(current_subject.relation))
                if current_subject.relation is not None
                else None
            )
            if current_ref is not None:
                for parent_ref, userset_cost, tuple_relation in semantic_parents.get(
                    current_ref,
                    (),
                ):
                    userset_depth = current_depth + userset_cost
                    if userset_depth > self._max_depth:
                        continue

                    if tuple_relation is None:
                        userset_subjects = (
                            Subject.from_parts(
                                parent_ref[0],
                                str(current_subject.id),
                                parent_ref[1],
                            ),
                        )
                    else:
                        userset_subjects = tuple(
                            Subject.from_parts(
                                str(relation_tuple.object.namespace),
                                str(relation_tuple.object.id),
                                parent_ref[1],
                            )
                            for relation_tuple in self._relations.read_reverse(
                                TupleFilter(
                                    object_type=parent_ref[0],
                                    relation=tuple_relation,
                                    subject_type=str(current_subject.namespace),
                                    subject_id=str(current_subject.id),
                                    subject_relation=(
                                        TupleFilter.DIRECT_SUBJECT_RELATION
                                    ),
                                ),
                                context=context,
                            )
                        )

                    for userset_subject in userset_subjects:
                        if not self._userset_semantically_reachable(
                            userset_subject=userset_subject,
                            subject=subject,
                            context=context,
                        ):
                            continue

                        seen_depth = seen_depths.get(userset_subject)
                        if seen_depth is not None and seen_depth <= userset_depth:
                            continue

                        seen_depths[userset_subject] = userset_depth
                        worklist.append((userset_subject, userset_depth))

            exact_subject_filter = TupleFilter.from_subject(current_subject)
            for relation_tuple in self._relations.read_reverse(
                TupleFilter.from_subject_bucket(current_subject),
                context=context,
            ):
                if not exact_subject_filter.matches(relation_tuple):
                    continue

                target_relation = str(relation_tuple.relation)
                if (
                    str(relation_tuple.object.namespace) == resource_type
                    and target_relation == relation
                ):
                    resources.add(relation_tuple.object)

                userset_ref = (
                    str(relation_tuple.object.namespace),
                    target_relation,
                )
                if userset_ref not in reachable_usersets:
                    continue

                userset_subject = Subject.from_parts(
                    str(relation_tuple.object.namespace),
                    str(relation_tuple.object.id),
                    target_relation,
                )
                if not self._userset_semantically_reachable(
                    userset_subject=userset_subject,
                    subject=subject,
                    context=context,
                ):
                    continue

                userset_depth = current_depth + 1
                if userset_depth > self._max_depth:
                    continue

                seen_depth = seen_depths.get(userset_subject)
                if seen_depth is not None and seen_depth <= userset_depth:
                    continue

                seen_depths[userset_subject] = userset_depth
                worklist.append((userset_subject, userset_depth))

        return resources

    def _lookup_tuple_to_userset(
        self,
        *,
        resource_type: str,
        tuple_relation: str,
        computed_relation: str,
        subject: Subject,
        context: ReadContext,
        depth: int,
    ) -> set[Obj]:
        resources: set[Obj] = set()
        for target_type in self._model.tuple_to_userset_target_types(
            resource_type=resource_type,
            tuple_relation=tuple_relation,
        ):
            parent_resources = self._lookup_relation(
                resource_type=target_type,
                relation=computed_relation,
                subject=subject,
                context=context,
                depth=depth + 1,
            )
            for parent in parent_resources:
                parent_subject = Subject.from_object(parent)
                resources.update(
                    t.object
                    for t in self._relations.read_reverse(
                        TupleFilter(
                            object_type=resource_type,
                            relation=tuple_relation,
                            subject_type=str(parent_subject.namespace),
                            subject_id=str(parent_subject.id),
                            subject_relation=TupleFilter.DIRECT_SUBJECT_RELATION,
                        ),
                        context=context,
                    )
                )
        return resources

    def _reachable_userset_refs(
        self,
        *,
        resource_type: str,
        relation: str,
    ) -> set[tuple[str, str]]:
        reachable = set(
            self._model.allowed_userset_refs(
                resource_type=resource_type,
                relation=relation,
            )
        )
        worklist = list(reachable)
        next_ref = 0

        while next_ref < len(worklist):
            subject_type, subject_relation = worklist[next_ref]
            next_ref += 1

            nested_refs = self._model.allowed_userset_refs(
                resource_type=subject_type,
                relation=subject_relation,
            )
            dependency_refs = tuple(
                child_ref
                for child_ref, _, _ in self._rewrite_dependency_refs(
                    resource_type=subject_type,
                    relation=subject_relation,
                )
            )
            for nested_ref in (*nested_refs, *dependency_refs):
                if nested_ref in reachable:
                    continue
                reachable.add(nested_ref)
                worklist.append(nested_ref)

        return reachable

    def _semantic_parent_refs(
        self,
        userset_refs: set[tuple[str, str]],
    ) -> dict[tuple[str, str], tuple[tuple[tuple[str, str], int, str | None], ...]]:
        parents: dict[
            tuple[str, str],
            list[tuple[tuple[str, str], int, str | None]],
        ] = {}
        for parent_ref in userset_refs:
            for child_ref, cost, tuple_relation in self._rewrite_dependency_refs(
                resource_type=parent_ref[0],
                relation=parent_ref[1],
            ):
                if child_ref not in userset_refs:
                    continue
                parents.setdefault(child_ref, []).append(
                    (parent_ref, cost, tuple_relation)
                )

        return {
            child_ref: tuple(parent_refs) for child_ref, parent_refs in parents.items()
        }

    def _rewrite_dependency_refs(
        self,
        *,
        resource_type: str,
        relation: str,
    ) -> tuple[tuple[tuple[str, str], int, str | None], ...]:
        dependencies = self._rewrite_dependencies(
            self._model.resolve(resource_type, relation),
            resource_type=resource_type,
            cost=0,
        )
        return tuple(
            (child_ref, cost, tuple_relation)
            for (child_ref, tuple_relation), cost in dependencies.items()
        )

    def _rewrite_dependencies(
        self,
        rewrite: RewriteRule,
        *,
        resource_type: str,
        cost: int,
    ) -> dict[tuple[tuple[str, str], str | None], int]:
        if isinstance(rewrite, ComputedUsersetRule):
            return {((resource_type, rewrite.relation), None): cost + 1}

        if isinstance(rewrite, TupleToUsersetRule):
            return {
                ((target_type, rewrite.computed_relation), rewrite.tuple_relation): (
                    cost + 1
                )
                for target_type in self._model.tuple_to_userset_target_types(
                    resource_type=resource_type,
                    tuple_relation=rewrite.tuple_relation,
                )
            }

        if isinstance(rewrite, (UnionRule, IntersectionRule)):
            dependencies: dict[tuple[tuple[str, str], str | None], int] = {}
            for child in rewrite.children:
                self._merge_dependency_costs(
                    dependencies,
                    self._rewrite_dependencies(
                        child,
                        resource_type=resource_type,
                        cost=cost + 1,
                    ),
                )
            return dependencies

        if isinstance(rewrite, ExclusionRule):
            return self._rewrite_dependencies(
                rewrite.base,
                resource_type=resource_type,
                cost=cost + 1,
            )

        return {}

    @staticmethod
    def _merge_dependency_costs(
        target: dict[tuple[tuple[str, str], str | None], int],
        source: dict[tuple[tuple[str, str], str | None], int],
    ) -> None:
        for dependency, cost in source.items():
            existing_cost = target.get(dependency)
            if existing_cost is None or cost < existing_cost:
                target[dependency] = cost

    def _userset_semantically_reachable(
        self,
        *,
        userset_subject: Subject,
        subject: Subject,
        context: ReadContext,
    ) -> bool:
        assert userset_subject.relation is not None
        rewrite = self._model.resolve(
            str(userset_subject.namespace),
            str(userset_subject.relation),
        )
        if isinstance(rewrite, (DirectRule, ThisRule)):
            return True

        return self._checker.check(
            CheckRequest(
                object_type=str(userset_subject.namespace),
                object_id=str(userset_subject.id),
                relation=str(userset_subject.relation),
                subject_type=str(subject.namespace),
                subject_id=str(subject.id),
            ),
            context=context,
        ).allowed

    @staticmethod
    def _direct_subject_matches(subject: Subject) -> tuple[Subject, ...]:
        if str(subject.id) == "*":
            return (subject,)
        return (
            subject,
            Subject(NamespaceId(str(subject.namespace)), EntityId("*")),
        )
