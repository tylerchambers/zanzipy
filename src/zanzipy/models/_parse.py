"""Small parsing helpers shared by model value objects."""


def split_entity_ref(
    value: str,
    *,
    kind: str,
    error_type: type[ValueError],
) -> tuple[str, str]:
    """Split a ``namespace:id`` reference without validating its components."""

    namespace, separator, entity_id = value.partition(":")
    if separator == "":
        raise error_type(f"{kind} must be in 'namespace:id' form")
    return namespace, entity_id
