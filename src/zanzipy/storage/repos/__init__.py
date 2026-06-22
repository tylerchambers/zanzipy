from .abstract import RelationRepository
from .concrete.memory.relations import InMemoryRelationRepository

__all__ = [
    "InMemoryRelationRepository",
    "RelationRepository",
]
