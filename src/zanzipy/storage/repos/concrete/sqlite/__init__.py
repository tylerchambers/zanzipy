"""SQLite-backed repository implementations.

This module uses Python's standard library 'sqlite3' and introduces no new
runtime dependencies for the core package. Import directly when needed:

    from zanzipy.storage.repos.concrete.sqlite import SQLiteRelationRepository
"""

from .relations import SQLiteRelationRepository

__all__ = [
    "SQLiteRelationRepository",
]
