from typing import TYPE_CHECKING

from zanzipy.engine.resolver import RuleResolver
from zanzipy.models import EntityId, NamespaceId, Obj, Relation, Subject, TupleFilter
from zanzipy.schema.relations import RelationDef
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


class LookupEngine:
    """Evaluates reverse LookupResources traversals from a subject to objects."""

    def __init__(
        self,
        *,
        relations_repository: RelationRepository,
        schema: SchemaRegistry,
        max_depth: int = 25,
        compiled_rules_cache: CompiledRuleCache[RewriteRule] | None = None,
    ) -> None:
        """Create a reverse lookup engine over a repository and schema."""
        self._relations = relations_repository
        self._schema = schema
        self._max_depth = max_depth
        self._resolver = RuleResolver(
            schema=schema,
            compiled_rules_cache=compiled_rules_cache,
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

        resources = self._lookup_recursive(
            resource_type=resource_type,
            relation=permission,
            subject=subject,
            context=context,
            depth=0,
            visited=set(),
            track_state=True,
        )
        return sorted(resources, key=str)

    def _lookup_recursive(
        self,
        *,
        resource_type: str,
        relation: str,
        subject: Subject,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
        track_state: bool,
    ) -> set[Obj]:
        if depth > self._max_depth:
            return set()

        try:
            rewrite = self._resolver.resolve(resource_type, relation)
        except ValueError:
            return set()

        if not track_state or isinstance(rewrite, (DirectRule, ThisRule)):
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
                visited=visited,
            )

        if isinstance(rewrite, ComputedUsersetRule):
            return self._lookup_recursive(
                resource_type=resource_type,
                relation=rewrite.relation,
                subject=subject,
                context=context,
                depth=depth + 1,
                visited=visited,
                track_state=True,
            )

        if isinstance(rewrite, TupleToUsersetRule):
            return self._lookup_tuple_to_userset(
                resource_type=resource_type,
                tuple_relation=rewrite.tuple_relation,
                computed_relation=rewrite.computed_relation,
                subject=subject,
                context=context,
                depth=depth,
                visited=visited,
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
        visited: set[tuple[str, str, str]],
    ) -> set[Obj]:
        resources: set[Obj] = set()
        for direct_subject in self._direct_subject_matches(subject):
            resources.update(
                t.object
                for t in self._relations.read_reverse(
                    TupleFilter(
                        object_type=resource_type,
                        relation=relation,
                        subject_type=str(direct_subject.namespace),
                        subject_id=str(direct_subject.id),
                        subject_relation=TupleFilter.DIRECT_SUBJECT_RELATION,
                    ),
                    context=context,
                )
            )

        for userset in self._containing_usersets(
            resource_type=resource_type,
            relation=relation,
            subject=subject,
            context=context,
            depth=depth,
            visited=visited,
        ):
            resources.update(
                t.object
                for t in self._relations.read_reverse(
                    TupleFilter(
                        object_type=resource_type,
                        relation=relation,
                        subject_type=str(userset.namespace),
                        subject_id=str(userset.id),
                        subject_relation=str(userset.relation),
                    ),
                    context=context,
                )
            )

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
        visited: set[tuple[str, str, str]],
    ) -> set[Obj]:
        resources: set[Obj] = set()
        for target_type in self._tuple_to_userset_target_types(
            resource_type=resource_type,
            tuple_relation=tuple_relation,
        ):
            parent_resources = self._lookup_recursive(
                resource_type=target_type,
                relation=computed_relation,
                subject=subject,
                context=context,
                depth=depth + 1,
                visited=visited,
                track_state=False,
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

    def _containing_usersets(
        self,
        *,
        resource_type: str,
        relation: str,
        subject: Subject,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str]],
    ) -> set[Subject]:
        usersets: set[Subject] = set()
        for subject_type, subject_relation in self._allowed_userset_refs(
            resource_type,
            relation,
        ):
            containing_objects = self._lookup_recursive(
                resource_type=subject_type,
                relation=subject_relation,
                subject=subject,
                context=context,
                depth=depth + 1,
                visited=visited,
                track_state=False,
            )
            usersets.update(
                Subject.from_parts(
                    str(obj.namespace),
                    str(obj.id),
                    subject_relation,
                )
                for obj in containing_objects
            )
        return usersets

    def _allowed_userset_refs(
        self,
        resource_type: str,
        relation: str,
    ) -> tuple[tuple[str, str], ...]:
        try:
            definition = self._schema.get_definition(resource_type, relation)
        except ValueError:
            return ()
        if not isinstance(definition, RelationDef):
            return ()

        refs: set[tuple[str, str]] = set()
        for subject_ref in definition.allowed_subjects:
            if subject_ref.relation is None:
                continue
            refs.add((str(subject_ref.namespace), str(subject_ref.relation)))
        return tuple(refs)

    def _tuple_to_userset_target_types(
        self,
        *,
        resource_type: str,
        tuple_relation: str,
    ) -> tuple[str, ...]:
        try:
            definition = self._schema.get_definition(resource_type, tuple_relation)
        except ValueError:
            return ()
        if not isinstance(definition, RelationDef):
            return ()

        target_types: set[str] = set()
        for subject_ref in definition.allowed_subjects:
            if subject_ref.relation is not None or subject_ref.wildcard:
                continue
            target_types.add(str(subject_ref.namespace))
        return tuple(target_types)

    @staticmethod
    def _direct_subject_matches(subject: Subject) -> tuple[Subject, ...]:
        if str(subject.id) == "*":
            return (subject,)
        return (
            subject,
            Subject(NamespaceId(str(subject.namespace)), EntityId("*")),
        )
