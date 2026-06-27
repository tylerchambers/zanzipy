from typing import Protocol


class RedisLike(Protocol):
    """Minimal Redis client contract used by cache implementations."""

    def get(self, key: str) -> bytes | str | None:
        """Return a stored value, or ``None`` for a cache miss."""
        ...

    def set(self, key: str, value: bytes | str, ex: int | None = None) -> bool:
        """Store a value with an optional Redis expiration in seconds."""
        ...

    def delete(self, *keys: str) -> int:
        """Remove keys and return the number deleted by the client."""
        ...

    def ping(self) -> bool:
        """Return whether the backing Redis connection is healthy."""
        ...

    def close(self) -> None:
        """Close the backing Redis connection."""
        ...
