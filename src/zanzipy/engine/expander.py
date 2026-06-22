from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from zanzipy.models.id import EntityId
from zanzipy.models.namespace import NamespaceId
from zanzipy.models.object import Obj
from zanzipy.models.relation import Relation
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


@dataclass(frozen=True, slots=True)
class SubjectSet:
    """Result of expanding a relation: aggregated subjects.

    - users: direct user subjects in canonical string form (e.g., "user:alice")
    - usersets: subject-set anchors in canonical form (e.g., "group:eng#member")
    """

    users: set[str] = field(default_factory=set)
    usersets: set[str] = field(default_factory=set)

    def union(self, other: SubjectSet) -> SubjectSet:
        return SubjectSet(
            users=set(self.users | other.users),
            usersets=set(self.usersets | other.usersets),
        )

    def intersection(self, other: SubjectSet) -> SubjectSet:
        return SubjectSet(
            users=set(self.users & other.users),
            usersets=set(self.usersets & other.usersets),
        )

    def difference(self, other: SubjectSet) -> SubjectSet:
        return SubjectSet(
            users=set(self.users - other.users),
            usersets=set(self.usersets - other.usersets),
        )


class ExpansionEngine:
    """Expands a relation/permission into the set of subjects that grant it.

    This computes a conservative aggregation of subjects without fully expanding
    usersets into their member principals. The output separates direct user
    principals from subject-set anchors.
    """

    def __init__(
        self,
        *,
        relations_repository: RelationRepository,
        schema: SchemaRegistry,
        max_depth: int = 25,
        compiled_rules_cache: CompiledRuleCache[RewriteRule] | None = None,
    ) -> None:
        self._relations = relations_repository
        self._schema = schema
        self._max_depth = max_depth
        self._compiled_cache = compiled_rules_cache

    def expand(self, object_type: str, object_id: str, relation: str) -> SubjectSet:
        """Expand subjects that grant relation/permission on the object.

        Validates inputs using existing value objects and schema definitions.
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
            depth=0,
            visited=visited,
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

    def _expand_recursive(
        self,
        *,
        object_type: str,
        object_id: str,
        relation: str,
        depth: int,
        visited: set[tuple[str, str, str]],
    ) -> SubjectSet:
        key = (object_type, object_id, relation)
        if key in visited or depth > self._max_depth:
            return SubjectSet()
        visited.add(key)

        rewrite = self._resolve_rewrite(object_type, relation)
        return self._evaluate_rule(
            rewrite=rewrite,
            object_type=object_type,
            object_id=object_id,
            depth=depth,
            visited=visited,
            current_relation=relation,
        )

    def _evaluate_rule(
        self,
        *,
        rewrite: RewriteRule,
        object_type: str,
        object_id: str,
        depth: int,
        visited: set[tuple[str, str, str]],
        current_relation: str,
    ) -> SubjectSet:
        if isinstance(rewrite, (DirectRule, ThisRule)):
            return self._expand_direct(
                object_type=object_type,
                object_id=object_id,
                effective_relation=current_relation,
            )

        if isinstance(rewrite, ComputedUsersetRule):
            return self._expand_recursive(
                object_type=object_type,
                object_id=object_id,
                relation=rewrite.relation,
                depth=depth + 1,
                visited=visited,
            )

        if isinstance(rewrite, TupleToUsersetRule):
            return self._expand_tuple_to_userset(
                object_type=object_type,
                object_id=object_id,
                tuple_relation=rewrite.tuple_relation,
                computed_relation=rewrite.computed_relation,
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
                    depth=depth + 1,
                    visited=visited,
                    current_relation=current_relation,
                )
                result = result.union(child_set)
            return result

        if isinstance(rewrite, IntersectionRule):
            accum: SubjectSet | None = None
            for child in rewrite.children:
                child_set = self._evaluate_rule(
                    rewrite=child,
                    object_type=object_type,
                    object_id=object_id,
                    depth=depth + 1,
                    visited=visited,
                    current_relation=current_relation,
                )
                accum = child_set if accum is None else accum.intersection(child_set)
            return accum if accum is not None else SubjectSet()

        if isinstance(rewrite, ExclusionRule):
            base = self._evaluate_rule(
                rewrite=rewrite.base,
                object_type=object_type,
                object_id=object_id,
                depth=depth + 1,
                visited=visited,
                current_relation=current_relation,
            )
            subtract = self._evaluate_rule(
                rewrite=rewrite.subtract,
                object_type=object_type,
                object_id=object_id,
                depth=depth + 1,
                visited=visited,
                current_relation=current_relation,
            )
            return base.difference(subtract)

        # Unknown node
        return SubjectSet()

    def _expand_direct(
        self,
        *,
        object_type: str,
        object_id: str,
        effective_relation: str,
    ) -> SubjectSet:
        obj = Obj(NamespaceId(object_type), EntityId(object_id))
        users: set[str] = set()
        usersets: set[str] = set()
        for t in self._relations.by_object(obj):
            if str(t.relation) != effective_relation:
                continue
            # Subject set anchor
            if t.subject.relation is not None:
                usersets.add(str(t.subject))
                continue
            # Direct subject; capture "user:" explicitly, but include others too
            if str(t.subject.namespace) == "user":
                users.add(f"{t.subject.namespace}:{t.subject.id}")
            else:
                # Non-user principals appear as direct subjects; represent as
                # userset-like
                usersets.add(f"{t.subject.namespace}:{t.subject.id}")
        return SubjectSet(users=users, usersets=usersets)

    def _expand_tuple_to_userset(
        self,
        *,
        object_type: str,
        object_id: str,
        tuple_relation: str,
        computed_relation: str,
        depth: int,
        visited: set[tuple[str, str, str]],
    ) -> SubjectSet:
        obj = Obj(NamespaceId(object_type), EntityId(object_id))
        result = SubjectSet()
        for t in self._relations.by_object(obj):
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
                depth=depth + 1,
                visited=visited,
            )
            result = result.union(child)
        return result

    def _resolve_rewrite(self, object_type: str, relation: str) -> RewriteRule:
        # Check compiled cache first
        cached = self._get_compiled_cache_if_available(object_type, relation)
        if cached is not None and isinstance(cached, RewriteRule):
            return cached

        # Resolve from schema
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
                raise ValueError(f"Permission has no rewrite: {object_type}:{relation}")
            result = RewriteRule.from_dict(rewrite_dict)
            result = self._set_compiled_cache_if_available(
                object_type, relation, result
            )
            return result
        raise ValueError(
            f"Unknown definition type for {object_type}:{relation}: {def_type!r}"
        )
