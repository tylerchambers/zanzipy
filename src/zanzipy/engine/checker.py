from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from zanzipy.engine.rewrite_dispatch import RewriteRuleDispatcher
from zanzipy.models import (
    CheckRequest,
    CheckResponse,
    EntityId,
    NamespaceId,
    Obj,
    TupleFilter,
)

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


@dataclass(slots=True)
class _Counters:
    """Mutable request-scoped metrics for a check traversal."""

    tuples_examined: int = 0
    max_depth_reached: int = 0


class _CheckResult(Enum):
    """Internal tri-state result for recursive authorization traversal."""

    ALLOW = 1
    DENY = 2
    UNKNOWN = 3


class CheckEngine(RewriteRuleDispatcher):
    """Evaluates Zanzibar relation and permission checks at a repository revision.

    The engine resolves compiled rewrites, follows subject-set edges, short-circuits
    boolean operators where possible, and bounds traversal with cycle and depth
    checks so callers receive a deterministic authorization result.
    """

    def __init__(
        self,
        *,
        relations_repository: RelationRepository,
        authorization_model: CompiledAuthorizationModel,
        max_depth: int = 25,
        enable_debug: bool = False,
    ) -> None:
        """Create a checker over a relation repository and compiled model."""
        self._relations = relations_repository
        self._max_depth = max_depth
        self._enable_debug = enable_debug
        self._model = authorization_model

    def check(
        self,
        request: CheckRequest,
        *,
        context: ReadContext,
    ) -> CheckResponse:
        """Evaluate one check request in the supplied tenant revision context.

        Debug traces and counters are populated only when the engine was created
        with debug support enabled.
        """
        visited: set[tuple[str, str, str, str, str]] = set()
        debug_trace: list[str] | None = [] if self._enable_debug else None
        if debug_trace is not None:
            debug_trace.append(
                f"context tenant={context.tenant} revision={context.revision}"
            )
        counters = _Counters()

        result = self._check_recursive(
            object_type=request.object_type,
            object_id=request.object_id,
            relation=request.relation,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            context=context,
            depth=0,
            visited=visited,
            debug_trace=debug_trace,
            counters=counters,
        )

        return CheckResponse(
            allowed=result is _CheckResult.ALLOW,
            debug_trace=debug_trace,
            depth_reached=counters.max_depth_reached,
            tuples_examined=counters.tuples_examined,
        )

    def _check_recursive(
        self,
        *,
        object_type: str,
        object_id: str,
        relation: str,
        subject_type: str,
        subject_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
    ) -> _CheckResult:
        """Internal recursive check with cycle detection and depth limiting."""

        counters.max_depth_reached = max(counters.max_depth_reached, depth)
        key = (object_type, object_id, relation, subject_type, subject_id)
        if key in visited:
            if debug_trace is not None:
                debug_trace.append(f"{'  ' * depth}Cycle detected")
            return _CheckResult.UNKNOWN
        if depth > self._max_depth:
            if debug_trace is not None:
                debug_trace.append(f"{'  ' * depth}Max depth reached: {depth}")
            return _CheckResult.UNKNOWN

        visited.add(key)
        try:
            if debug_trace is not None:
                msg = (
                    f"{'  ' * depth}-> check {object_type}:{object_id}"
                    f"#{relation}@{subject_type}:{subject_id}"
                )
                debug_trace.append(msg)

            # Resolve the compiled rewrite rule for (object_type, relation)
            try:
                rewrite = self._model.resolve(object_type, relation)
            except ValueError as exc:
                if debug_trace is not None:
                    debug_trace.append(f"{'  ' * depth}Error: {exc}")
                return _CheckResult.UNKNOWN

            return self._evaluate_rule(
                rewrite=rewrite,
                object_type=object_type,
                object_id=object_id,
                subject_type=subject_type,
                subject_id=subject_id,
                context=context,
                depth=depth,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
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
        subject_type: str,
        subject_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
        current_relation: str,
    ) -> _CheckResult:
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
            subject_type=subject_type,
            subject_id=subject_id,
            context=context,
            depth=depth,
            visited=visited,
            debug_trace=debug_trace,
            counters=counters,
            current_relation=current_relation,
        )

    def _evaluate_direct_or_this_rule(
        self,
        _rewrite: DirectRule | ThisRule,
        *,
        object_type: str,
        object_id: str,
        subject_type: str,
        subject_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
        current_relation: str,
    ) -> _CheckResult:
        return self._check_direct(
            object_type=object_type,
            object_id=object_id,
            subject_type=subject_type,
            subject_id=subject_id,
            context=context,
            depth=depth,
            visited=visited,
            debug_trace=debug_trace,
            counters=counters,
            effective_relation=current_relation,
        )

    def _evaluate_computed_userset_rule(
        self,
        rewrite: ComputedUsersetRule,
        *,
        object_type: str,
        object_id: str,
        subject_type: str,
        subject_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
        current_relation: str,
    ) -> _CheckResult:
        return self._check_recursive(
            object_type=object_type,
            object_id=object_id,
            relation=rewrite.relation,
            subject_type=subject_type,
            subject_id=subject_id,
            context=context,
            depth=depth + 1,
            visited=visited,
            debug_trace=debug_trace,
            counters=counters,
        )

    def _evaluate_tuple_to_userset_rule(
        self,
        rewrite: TupleToUsersetRule,
        *,
        object_type: str,
        object_id: str,
        subject_type: str,
        subject_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
        current_relation: str,
    ) -> _CheckResult:
        return self._check_tuple_to_userset(
            object_type=object_type,
            object_id=object_id,
            tuple_relation=rewrite.tuple_relation,
            computed_relation=rewrite.computed_relation,
            subject_type=subject_type,
            subject_id=subject_id,
            context=context,
            depth=depth,
            visited=visited,
            debug_trace=debug_trace,
            counters=counters,
        )

    def _evaluate_union_rule(
        self,
        rewrite: UnionRule,
        *,
        object_type: str,
        object_id: str,
        subject_type: str,
        subject_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
        current_relation: str,
    ) -> _CheckResult:
        saw_unknown = False
        for child in rewrite.children:
            result = self._evaluate_rule(
                rewrite=child,
                object_type=object_type,
                object_id=object_id,
                subject_type=subject_type,
                subject_id=subject_id,
                context=context,
                depth=depth + 1,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
                current_relation=current_relation,
            )
            if result is _CheckResult.ALLOW:
                return _CheckResult.ALLOW
            if result is _CheckResult.UNKNOWN:
                saw_unknown = True
        return _CheckResult.UNKNOWN if saw_unknown else _CheckResult.DENY

    def _evaluate_intersection_rule(
        self,
        rewrite: IntersectionRule,
        *,
        object_type: str,
        object_id: str,
        subject_type: str,
        subject_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
        current_relation: str,
    ) -> _CheckResult:
        saw_unknown = False
        for child in rewrite.children:
            result = self._evaluate_rule(
                rewrite=child,
                object_type=object_type,
                object_id=object_id,
                subject_type=subject_type,
                subject_id=subject_id,
                context=context,
                depth=depth + 1,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
                current_relation=current_relation,
            )
            if result is _CheckResult.DENY:
                return _CheckResult.DENY
            if result is _CheckResult.UNKNOWN:
                saw_unknown = True
        return _CheckResult.UNKNOWN if saw_unknown else _CheckResult.ALLOW

    def _evaluate_exclusion_rule(
        self,
        rewrite: ExclusionRule,
        *,
        object_type: str,
        object_id: str,
        subject_type: str,
        subject_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
        current_relation: str,
    ) -> _CheckResult:
        base_result = self._evaluate_rule(
            rewrite=rewrite.base,
            object_type=object_type,
            object_id=object_id,
            subject_type=subject_type,
            subject_id=subject_id,
            context=context,
            depth=depth + 1,
            visited=visited,
            debug_trace=debug_trace,
            counters=counters,
            current_relation=current_relation,
        )
        if base_result is _CheckResult.DENY:
            return _CheckResult.DENY
        if base_result is _CheckResult.UNKNOWN:
            return _CheckResult.UNKNOWN

        subtract_result = self._evaluate_rule(
            rewrite=rewrite.subtract,
            object_type=object_type,
            object_id=object_id,
            subject_type=subject_type,
            subject_id=subject_id,
            context=context,
            depth=depth + 1,
            visited=visited,
            debug_trace=debug_trace,
            counters=counters,
            current_relation=current_relation,
        )
        if subtract_result is _CheckResult.ALLOW:
            return _CheckResult.DENY
        if subtract_result is _CheckResult.UNKNOWN:
            return _CheckResult.UNKNOWN
        return _CheckResult.ALLOW

    def _check_direct(
        self,
        *,
        object_type: str,
        object_id: str,
        subject_type: str,
        subject_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
        effective_relation: str,
    ) -> _CheckResult:
        """Check direct tuples and expand usersets.

        Logic:
        1. Iterate tuples for the object
        2. Filter by the effective relation name
        3. For subject sets (subject has a relation), recursively check membership
        """

        obj = Obj(NamespaceId(object_type), EntityId(object_id))
        saw_unknown = False
        for t in self._relations.read(TupleFilter.from_object(obj), context=context):
            # Filter matching relation on the object
            if str(t.relation) != effective_relation:
                continue

            counters.tuples_examined += 1

            # Direct subject: exact match or namespace wildcard
            if t.subject.relation is None and str(t.subject.namespace) == subject_type:
                tuple_subject_id = str(t.subject.id)
                if tuple_subject_id == subject_id or tuple_subject_id == "*":
                    if debug_trace is not None:
                        debug_trace.append(
                            f"{'  ' * (depth + 1)}matched direct tuple: {t}"
                        )
                    return _CheckResult.ALLOW

            # Subject set: recurse on the subject's relation
            if t.subject.relation is not None:
                result = self._check_recursive(
                    object_type=str(t.subject.namespace),
                    object_id=str(t.subject.id),
                    relation=str(t.subject.relation),
                    subject_type=subject_type,
                    subject_id=subject_id,
                    context=context,
                    depth=depth + 1,
                    visited=visited,
                    debug_trace=debug_trace,
                    counters=counters,
                )
                if result is _CheckResult.ALLOW:
                    return _CheckResult.ALLOW
                if result is _CheckResult.UNKNOWN:
                    saw_unknown = True

        return _CheckResult.UNKNOWN if saw_unknown else _CheckResult.DENY

    def _check_tuple_to_userset(
        self,
        *,
        object_type: str,
        object_id: str,
        tuple_relation: str,
        computed_relation: str,
        subject_type: str,
        subject_id: str,
        context: ReadContext,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
    ) -> _CheckResult:
        """Evaluate a tuple-to-userset step.

        Follow relation ``tuple_relation`` from the current object to subjects
        that are objects, then evaluate ``computed_relation`` on those objects
        for the same subject.
        """

        obj = Obj(NamespaceId(object_type), EntityId(object_id))
        saw_unknown = False
        for t in self._relations.read(TupleFilter.from_object(obj), context=context):
            if str(t.relation) != tuple_relation:
                continue

            counters.tuples_examined += 1

            # Only consider tuples where the subject is an object reference
            if t.subject.relation is not None:
                continue

            result = self._check_recursive(
                object_type=str(t.subject.namespace),
                object_id=str(t.subject.id),
                relation=computed_relation,
                subject_type=subject_type,
                subject_id=subject_id,
                context=context,
                depth=depth + 1,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
            )
            if result is _CheckResult.ALLOW:
                return _CheckResult.ALLOW
            if result is _CheckResult.UNKNOWN:
                saw_unknown = True

        return _CheckResult.UNKNOWN if saw_unknown else _CheckResult.DENY
