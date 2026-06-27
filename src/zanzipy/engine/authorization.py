from typing import TYPE_CHECKING

from zanzipy.engine.checker import CheckEngine
from zanzipy.engine.expander import ExpansionEngine
from zanzipy.engine.lookup import LookupEngine
from zanzipy.schema.compiled import CompiledAuthorizationModel

if TYPE_CHECKING:
    from zanzipy.engine.expander import SubjectSet
    from zanzipy.models import (
        CheckRequest,
        CheckResponse,
        LookupResourcesRequest,
        LookupResourcesResponse,
    )
    from zanzipy.schema.registry import SchemaRegistry
    from zanzipy.schema.rules import RewriteRule
    from zanzipy.storage.cache.abstract.rules import CompiledRuleCache
    from zanzipy.storage.cache.abstract.tuples import TupleCache
    from zanzipy.storage.repos.abstract.relations import RelationRepository
    from zanzipy.storage.revision import ReadContext


class AuthorizationEngine:
    """Cohesive boundary for authorization reads over one schema and repository.

    The engine suite owns one compiled authorization model shared by check,
    expand, and lookup operations, plus shared traversal limits, debug settings,
    repository decorators, and cache wiring. Callers supply an already-resolved
    read context so tenant and revision selection stays outside traversal.
    """

    def __init__(
        self,
        *,
        relations_repository: RelationRepository,
        schema: SchemaRegistry,
        authorization_model: CompiledAuthorizationModel | None = None,
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
        if authorization_model is None:
            authorization_model = CompiledAuthorizationModel.from_schema(
                schema,
                compiled_rules_cache=compiled_rules_cache,
            )

        self._authorization_model = authorization_model
        self._checker = CheckEngine(
            relations_repository=relations_repository,
            authorization_model=authorization_model,
            max_depth=max_depth,
            enable_debug=enable_debug,
        )
        self._expander = ExpansionEngine(
            relations_repository=relations_repository,
            authorization_model=authorization_model,
            max_depth=max_depth,
        )
        self._lookup = LookupEngine(
            relations_repository=relations_repository,
            authorization_model=authorization_model,
            max_depth=max_depth,
            enable_debug=enable_debug,
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
    def authorization_model(self) -> CompiledAuthorizationModel:
        """Return the compiled model snapshot shared by all operations."""

        return self._authorization_model

    @property
    def max_depth(self) -> int:
        """Return the traversal depth limit shared by all operations."""

        return self._max_depth

    @property
    def enable_debug(self) -> bool:
        """Return whether traversal diagnostics are enabled for this boundary."""

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
        request: LookupResourcesRequest,
        *,
        context: ReadContext,
    ) -> LookupResourcesResponse:
        """Evaluate one typed LookupResources request at the supplied context."""

        return self._lookup.lookup_resources(request, context=context)
