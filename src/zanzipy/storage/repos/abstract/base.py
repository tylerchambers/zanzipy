"""Generic repository abstractions for storage backends.

This module defines a minimal, extensible, and well-typed base repository
interface that can be specialized for concrete domain models (e.g., relation
tuples, rewrite rules). It focuses on clear semantics over storage details.

Design goals:
- Keep the surface small but practical for most backends
- Provide safe defaults for bulk helpers
- Allow composite keys via a generic key type
- Avoid policy about pagination/cursors; leave to concrete repos
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from typing import Self


class BaseRepository[TEntity, TKey, TFilter](ABC):
    """Abstract base repository for typed entities.

    The repository is keyed by an arbitrary ``TKey`` which can represent simple
    or composite keys. Query operations accept a ``TFilter`` value whose exact
    structure is defined by concrete implementations (e.g., a dataclass or a
    lightweight mapping).

    Concrete repositories should document their key and filter types.
    """

    @abstractmethod
    def key_of(self, entity: TEntity) -> TKey:
        """Return the stable key for ``entity``.

        Implementations must ensure that ``key_of`` is consistent with the
        storage key used by ``get``/``delete_by_key``.
        """

    @abstractmethod
    def upsert(self, entity: TEntity) -> None:
        """Insert or replace ``entity`` in storage (idempotent)."""

    def upsert_many(self, entities: Iterable[TEntity]) -> None:
        """Bulk upsert convenience method.

        Implementations may override for batch efficiency.
        The default iterates and calls ``upsert`` per entity.
        """

        for entity in entities:
            self.upsert(entity)

    @abstractmethod
    def delete_by_key(self, key: TKey) -> bool:
        """Delete the entity identified by ``key``.

        Returns True if a record was deleted, False if it did not exist.
        """

    def delete(self, entity: TEntity) -> bool:
        """Delete by entity instance using its key."""

        return self.delete_by_key(self.key_of(entity))

    def delete_many_by_key(self, keys: Iterable[TKey]) -> int:
        """Bulk delete by keys. Returns the number of records deleted.

        Implementations may override for batch efficiency.
        """

        deleted = 0
        for key in keys:
            deleted += 1 if self.delete_by_key(key) else 0
        return deleted

    def delete_many(self, entities: Iterable[TEntity]) -> int:
        """Bulk delete by entities. Returns the number of records deleted."""

        # Materialize to avoid mutating underlying storage during iteration
        entity_list = list(entities)
        keys = [self.key_of(e) for e in entity_list]
        return self.delete_many_by_key(keys)

    def delete_where(self, filter: TFilter) -> int:
        """Delete all entities matching ``filter``. Returns count deleted.

        Default implementation streams matching entities and deletes by key.
        Implementations may override for a more efficient backend-native
        deletion when possible.
        """

        # Materialize to avoid concurrent modification during iteration
        to_delete_entities = list(self.find(filter))
        to_delete_keys = [self.key_of(entity) for entity in to_delete_entities]
        return self.delete_many_by_key(to_delete_keys)

    @abstractmethod
    def get(self, key: TKey) -> TEntity | None:
        """Fetch a single entity by key, or ``None`` if not found."""

    @abstractmethod
    def find(self, filter: TFilter) -> Iterable[TEntity]:
        """Iterate entities that match ``filter``.

        Implementations may return any iterable (e.g., generator or list).
        """

    def exists(self, key: TKey) -> bool:
        """Return True if an entity with ``key`` exists.

        Default implementation calls ``get``; override for efficiency.
        """

        return self.get(key) is not None

    def iter(self, filter: TFilter) -> Iterator[TEntity]:
        """Explicit iterator adapter over ``find`` for clarity."""

        yield from self.find(filter)

    def ping(self) -> bool:
        """Lightweight health check.

        Default returns True. Implementations may verify connectivity.
        """

        return True

    def info(self) -> dict[str, Any]:
        """Optional backend metadata for diagnostics/monitoring."""

        return {}

    def close(self) -> None:
        """Release any resources held by the repository (optional)."""

        # Default is a no-op
        return None

    # Context manager convenience
    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None
