"""SQLAlchemy-backed repository implementations (optional extra).

Requires installation with the extra:

    pip install zanzipy[sqlalchemy]
"""

from .relations import SQLAlchemyRelationRepository

__all__ = [
    "SQLAlchemyRelationRepository",
]
