from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from zanzipy.models.check import CheckRequest, CheckResponse
from zanzipy.models.id import EntityId
from zanzipy.models.namespace import NamespaceId
from zanzipy.models.object import Obj
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


@dataclass(slots=True)
class _Counters:
    tuples_examined: int = 0


class CheckEngine:
    """
    Evaluates permission checks by traversing the relation graph.

    Algorithm:
    1. Resolve rewrite rule from the schema registry (with optional compiled cache)
    2. For Direct/This: check stored tuples and expand usersets
    3. For Union: short-circuit on first success
    4. For Intersection: require all children to succeed
    5. For Exclusion: base succeeds and subtract fails
    6. Recurse with cycle detection and depth limits
    """

    def __init__(
        self,
        *,
        relations_repository: RelationRepository[Any, Any],
        schema: SchemaRegistry,
        max_depth: int = 25,
        enable_debug: bool = False,
        compiled_rules_cache: CompiledRuleCache[RewriteRule] | None = None,
    ) -> None:
        self._relations = relations_repository
        self._schema = schema
        self._max_depth = max_depth
        self._enable_debug = enable_debug
        self._compiled_cache = compiled_rules_cache

    def check(self, request: CheckRequest) -> CheckResponse:
        """
        Main entry point for permission checks.
        Returns immediately on first positive result.
        """
        visited: set[tuple[str, str, str, str, str]] = set()
        debug_trace: list[str] | None = [] if self._enable_debug else None
        counters = _Counters()

        allowed = self._check_recursive(
            object_type=request.object_type,
            object_id=request.object_id,
            relation=request.relation,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            depth=0,
            visited=visited,
            debug_trace=debug_trace,
            counters=counters,
        )

        return CheckResponse(
            allowed=allowed,
            debug_trace=debug_trace,
            depth_reached=len(visited),
            tuples_examined=counters.tuples_examined,
        )

    def _set_compiled_cache_if_available(
        self, object_type: str, relation: str, rewrite: RewriteRule
    ) -> RewriteRule:
        if self._compiled_cache is not None:
            self._compiled_cache.set(object_type, relation, rewrite)
        return rewrite

    def _get_compiled_cache_if_available(
        self, object_type: str, relation: str
    ) -> RewriteRule | None:
        if self._compiled_cache is not None:
            return self._compiled_cache.get(object_type, relation)
        return None

    def _check_recursive(
        self,
        *,
        object_type: str,
        object_id: str,
        relation: str,
        subject_type: str,
        subject_id: str,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
    ) -> bool:
        """Internal recursive check with cycle detection and depth limiting."""

        key = (object_type, object_id, relation, subject_type, subject_id)
        if key in visited:
            return False
        if depth > self._max_depth:
            if debug_trace is not None:
                debug_trace.append(f"{'  ' * depth}Max depth reached: {depth}")
            return False

        visited.add(key)

        if debug_trace is not None:
            msg = (
                f"{'  ' * depth}-> check {object_type}:{object_id}"
                f"#{relation}@{subject_type}:{subject_id}"
            )
            debug_trace.append(msg)

        # Resolve the rewrite rule for (object_type, relation)
        try:
            rewrite = self._resolve_rewrite(object_type, relation)
        except ValueError as exc:
            if debug_trace is not None:
                debug_trace.append(f"{'  ' * depth}Error: {exc}")
            return False

        return self._evaluate_rule(
            rewrite=rewrite,
            object_type=object_type,
            object_id=object_id,
            subject_type=subject_type,
            subject_id=subject_id,
            depth=depth,
            visited=visited,
            debug_trace=debug_trace,
            counters=counters,
            current_relation=relation,
        )

    def _evaluate_rule(
        self,
        *,
        rewrite: RewriteRule,
        object_type: str,
        object_id: str,
        subject_type: str,
        subject_id: str,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
        current_relation: str,
    ) -> bool:
        """Dispatch evaluation based on rewrite node type."""

        if isinstance(rewrite, DirectRule):
            return self._check_direct(
                object_type=object_type,
                object_id=object_id,
                subject_type=subject_type,
                subject_id=subject_id,
                depth=depth,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
                effective_relation=current_relation,
            )

        if isinstance(rewrite, ThisRule):
            return self._check_direct(
                object_type=object_type,
                object_id=object_id,
                subject_type=subject_type,
                subject_id=subject_id,
                depth=depth,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
                effective_relation=current_relation,
            )

        if isinstance(rewrite, ComputedUsersetRule):
            return self._check_recursive(
                object_type=object_type,
                object_id=object_id,
                relation=rewrite.relation,
                subject_type=subject_type,
                subject_id=subject_id,
                depth=depth + 1,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
            )

        if isinstance(rewrite, TupleToUsersetRule):
            return self._check_tuple_to_userset(
                object_type=object_type,
                object_id=object_id,
                tuple_relation=rewrite.tuple_relation,
                computed_relation=rewrite.computed_relation,
                subject_type=subject_type,
                subject_id=subject_id,
                depth=depth,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
            )

        if isinstance(rewrite, UnionRule):
            for child in rewrite.children:
                if self._evaluate_rule(
                    rewrite=child,
                    object_type=object_type,
                    object_id=object_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    depth=depth + 1,
                    visited=visited,
                    debug_trace=debug_trace,
                    counters=counters,
                    current_relation=current_relation,
                ):
                    return True
            return False

        if isinstance(rewrite, IntersectionRule):
            for child in rewrite.children:
                ok = self._evaluate_rule(
                    rewrite=child,
                    object_type=object_type,
                    object_id=object_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    depth=depth + 1,
                    visited=visited,
                    debug_trace=debug_trace,
                    counters=counters,
                    current_relation=current_relation,
                )
                if not ok:
                    return False
            return True

        if isinstance(rewrite, ExclusionRule):
            base_ok = self._evaluate_rule(
                rewrite=rewrite.base,
                object_type=object_type,
                object_id=object_id,
                subject_type=subject_type,
                subject_id=subject_id,
                depth=depth + 1,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
                current_relation=current_relation,
            )
            if not base_ok:
                return False
            subtract_ok = self._evaluate_rule(
                rewrite=rewrite.subtract,
                object_type=object_type,
                object_id=object_id,
                subject_type=subject_type,
                subject_id=subject_id,
                depth=depth + 1,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
                current_relation=current_relation,
            )
            return not subtract_ok

        # Unknown node type at runtime
        return False

    def _check_direct(
        self,
        *,
        object_type: str,
        object_id: str,
        subject_type: str,
        subject_id: str,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
        effective_relation: str,
    ) -> bool:
        """Check direct tuples and expand usersets.

        Logic:
        1. Iterate tuples for the object
        2. Filter by the effective relation name
        3. For subject sets (subject has a relation), recursively check membership
        """

        obj = Obj(NamespaceId(object_type), EntityId(object_id))
        for t in self._relations.by_object(obj):
            # Filter matching relation on the object
            if str(t.relation) != effective_relation:
                continue

            counters.tuples_examined += 1

            # Direct subject: exact match
            if t.subject.relation is None and (
                str(t.subject.namespace) == subject_type
                and str(t.subject.id) == subject_id
            ):
                if debug_trace is not None:
                    debug_trace.append(f"{'  ' * (depth + 1)}matched direct tuple: {t}")
                return True

            # Subject set: recurse on the subject's relation
            if t.subject.relation is not None and self._check_recursive(
                object_type=str(t.subject.namespace),
                object_id=str(t.subject.id),
                relation=str(t.subject.relation),
                subject_type=subject_type,
                subject_id=subject_id,
                depth=depth + 1,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
            ):
                return True

        return False

    def _check_tuple_to_userset(
        self,
        *,
        object_type: str,
        object_id: str,
        tuple_relation: str,
        computed_relation: str,
        subject_type: str,
        subject_id: str,
        depth: int,
        visited: set[tuple[str, str, str, str, str]],
        debug_trace: list[str] | None,
        counters: _Counters,
    ) -> bool:
        """Evaluate a tuple-to-userset step.

        Follow relation ``tuple_relation`` from the current object to subjects
        that are objects, then evaluate ``computed_relation`` on those objects
        for the same subject.
        """

        obj = Obj(NamespaceId(object_type), EntityId(object_id))
        for t in self._relations.by_object(obj):
            if str(t.relation) != tuple_relation:
                continue

            counters.tuples_examined += 1

            # Only consider tuples where the subject is an object reference
            if t.subject.relation is not None:
                continue

            if self._check_recursive(
                object_type=str(t.subject.namespace),
                object_id=str(t.subject.id),
                relation=computed_relation,
                subject_type=subject_type,
                subject_id=subject_id,
                depth=depth + 1,
                visited=visited,
                debug_trace=debug_trace,
                counters=counters,
            ):
                return True

        return False

    def _resolve_rewrite(self, object_type: str, relation: str) -> RewriteRule:
        """Resolve a rewrite rule for ``(object_type, relation)``.

        Order of resolution:
        1) Schema registry relation/permission definition
        (with an optional compiled rule cache in front)
        """

        # Check compiled cache first
        cached = self._get_compiled_cache_if_available(object_type, relation)
        if cached is not None and isinstance(cached, RewriteRule):
            return cached

        # Resolve from schema registry
        rel_def = self._schema.get_relation_definition(object_type, relation)
        def_type = rel_def.get("type")
        if def_type == "relation":
            rewrite_dict = rel_def.get("rewrite")
            if rewrite_dict is None:
                result = DirectRule()
                result = self._set_compiled_cache_if_available(
                    object_type, relation, result
                )
                return result
            result = RewriteRule.from_dict(rewrite_dict)
            result = self._set_compiled_cache_if_available(
                object_type, relation, result
            )
            return result
        if def_type == "permission":
            rewrite_dict = rel_def.get("rewrite")
            if rewrite_dict is None:
                # Permissions must have rewrites per validation
                raise ValueError(f"Permission has no rewrite: {object_type}:{relation}")
            result = RewriteRule.from_dict(rewrite_dict)
            result = self._set_compiled_cache_if_available(
                object_type, relation, result
            )
            return result

        raise ValueError(
            f"Unknown definition type for {object_type}:{relation}: {def_type!r}"
        )

    @staticmethod
    def _current_relation_name(
        object_type: str,
        object_id: str,
        visited: set[tuple[str, str, str, str, str]],
    ) -> str:
        """Infer current relation name from the last visited key for this object.

        Since we dispatch into _evaluate_rule with the resolved rewrite already
        for a specific relation, we need that relation name when evaluating
        Direct/This. We extract it from the most recent matching visited key.
        """

        # Heuristic: the relation is the last visited entry for this object_type
        # This relies on the recursive call pattern where we add to 'visited'
        # before resolving the rewrite.
        for ot, oid, rel, _st, _sid in reversed(list(visited)):
            if ot == object_type and oid == object_id:
                return rel
        # Fallback shouldn't happen; return empty relation to avoid false positives
        return ""
