from typing import TYPE_CHECKING

from zanzipy.schema.rules import DirectRule, RewriteRule
from zanzipy.schema.types import SchemaDefinitionType

if TYPE_CHECKING:
    from zanzipy.schema.registry import SchemaRegistry
    from zanzipy.storage.cache.abstract.rules import CompiledRuleCache


class RuleResolver:
    """Turns registered relation or permission definitions into rewrite trees."""

    def __init__(
        self,
        *,
        schema: SchemaRegistry,
        compiled_rules_cache: CompiledRuleCache[RewriteRule] | None = None,
    ) -> None:
        """Create a resolver backed by a schema and optional rewrite cache."""
        self._schema = schema
        self._compiled_cache = compiled_rules_cache

    def resolve(self, object_type: str, relation: str) -> RewriteRule:
        """Return the rewrite rule the engines should evaluate for this edge."""
        rel_def = self._schema.get_relation_definition(object_type, relation)
        rewrite = self._rewrite_from_definition(object_type, relation, rel_def)

        if self._compiled_cache is None:
            return rewrite

        cached = self._compiled_cache.get(object_type, relation)
        if cached is not None and cached.to_dict() == rewrite.to_dict():
            return cached

        self._compiled_cache.set(object_type, relation, rewrite)
        return rewrite

    @staticmethod
    def _rewrite_from_definition(
        object_type: str, relation: str, rel_def: dict
    ) -> RewriteRule:
        """Convert one registry definition dictionary into a rewrite rule."""
        raw_type = rel_def.get("type")
        try:
            def_type = SchemaDefinitionType(raw_type)
        except ValueError as exc:
            raise ValueError(
                f"Unknown definition type for {object_type}:{relation}: {raw_type!r}"
            ) from exc

        if def_type is SchemaDefinitionType.RELATION:
            rewrite_dict = rel_def.get("rewrite")
            if rewrite_dict is None:
                return DirectRule()
            return RewriteRule.from_dict(rewrite_dict)

        if def_type is SchemaDefinitionType.PERMISSION:
            rewrite_dict = rel_def.get("rewrite")
            if rewrite_dict is None:
                raise ValueError(f"Permission has no rewrite: {object_type}:{relation}")
            return RewriteRule.from_dict(rewrite_dict)

        raise AssertionError(f"Unhandled schema definition type: {def_type}")
