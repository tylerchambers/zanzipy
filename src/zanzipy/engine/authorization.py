from typing import TYPE_CHECKING

from zanzipy.engine.checker import CheckEngine
from zanzipy.engine.expander import ExpansionEngine
from zanzipy.engine.lookup import LookupEngine

if TYPE_CHECKING:
    from zanzipy.engine.expander import SubjectSet
    from zanzipy.models import CheckRequest, CheckResponse, Obj, Subject
    from zanzipy.schema.registry import SchemaRegistry
    from zanzipy.schema.rules import RewriteRule
    from zanzipy.storage.cache.abstract.rules import CompiledRuleCache
    from zanzipy.storage.cache.abstract.tuples import TupleCache
    from zanzipy.storage.repos.abstract.relations import RelationRepository
    from zanzipy.storage.revision import ReadContext


class AuthorizationEngine:
    """Cohesive boundary for authorization reads over one schema and repository.

    The engine suite owns check, expand, and lookup operations with shared
    traversal limits, debug settings, repository decorators, and compiled rule
    cache wiring. Callers supply an already-resolved read context so tenant and
    revision selection stays outside the authorization traversal boundary.
    """

    def __init__(
        self,
        *,
        relations_repository: RelationRepository,
        schema: SchemaRegistry,
        max_depth: int = 25,
        enable_debug: bool = False,
        compiled_rules_cache: CompiledRuleCache[RewriteRule] | None = None,
        tuple_cache: TupleCache | None = None,
    ) -> None:
        """Create all authorization operations over the same backing state."""

        if tuple_cache is not None:
            from zanzipy.storage.repos.decorators.cached_relations import (
                CachedRelationRepository,
            )

            relations_repository = CachedRelationRepository(
                backend=relations_repository,
                cache=tuple_cache,
            )

        self._relations_repository = relations_repository
        self._schema = schema
        self._max_depth = max_depth
        self._enable_debug = enable_debug
        self._checker = CheckEngine(
            relations_repository=relations_repository,
            schema=schema,
            max_depth=max_depth,
            enable_debug=enable_debug,
            compiled_rules_cache=compiled_rules_cache,
        )
        self._expander = ExpansionEngine(
            relations_repository=relations_repository,
            schema=schema,
            max_depth=max_depth,
            compiled_rules_cache=compiled_rules_cache,
        )
        self._lookup = LookupEngine(
            relations_repository=relations_repository,
            schema=schema,
            max_depth=max_depth,
            compiled_rules_cache=compiled_rules_cache,
        )

    @property
    def relations_repository(self) -> RelationRepository:
        """Return the repository shared by all authorization operations."""

        return self._relations_repository

    @property
    def schema(self) -> SchemaRegistry:
        """Return the schema registry shared by all authorization operations."""

        return self._schema

    @property
    def max_depth(self) -> int:
        """Return the traversal depth limit shared by all operations."""

        return self._max_depth

    @property
    def enable_debug(self) -> bool:
        """Return whether check diagnostics are enabled for this boundary."""

        return self._enable_debug

    def check(self, request: CheckRequest, *, context: ReadContext) -> CheckResponse:
        """Evaluate one authorization check at the supplied read context."""

        return self._checker.check(request, context=context)

    def expand(
        self,
        object_type: str,
        object_id: str,
        relation: str,
        *,
        context: ReadContext,
    ) -> SubjectSet:
        """Expand one object relation at the supplied read context."""

        return self._expander.expand(
            object_type,
            object_id,
            relation,
            context=context,
        )

    def lookup_resources(
        self,
        *,
        resource_type: str,
        permission: str,
        subject: Subject,
        context: ReadContext,
    ) -> list[Obj]:
        """Return resources granting ``permission`` to ``subject``."""

        return self._lookup.lookup_resources(
            resource_type=resource_type,
            permission=permission,
            subject=subject,
            context=context,
        )
