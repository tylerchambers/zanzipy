from enum import StrEnum


class SchemaDefinitionType(StrEnum):
    """Discriminator values for relation and permission schema definitions."""

    RELATION = "relation"
    PERMISSION = "permission"


class RewriteRuleType(StrEnum):
    """Discriminator values for serialized rewrite rule nodes."""

    THIS = "this"
    DIRECT = "direct"
    COMPUTED_USERSET = "computed_userset"
    TUPLE_TO_USERSET = "tuple_to_userset"
    UNION = "union"
    INTERSECTION = "intersection"
    EXCLUSION = "exclusion"
