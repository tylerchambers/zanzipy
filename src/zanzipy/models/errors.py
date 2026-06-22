class InvalidTupleFormatError(ValueError):
    """Raised when relation tuple components are invalid or malformed."""


class IdentifierValidationError(ValueError):
    """Raised when an identifier value is invalid."""


class EntityIdValidationError(ValueError):
    """Raised when an entity id value is invalid."""


class SubjectValidationError(ValueError):
    """Raised when a subject string or components are invalid."""
