from typing import TYPE_CHECKING, Any

from zanzipy.integration.mixins import AuthorizableResource
from zanzipy.models.id import EntityId
from zanzipy.models.namespace import NamespaceId
from zanzipy.models.object import Obj

if TYPE_CHECKING:
    from collections.abc import Callable


def authorizable_resource[T](namespace: str) -> Callable[[type[T]], type[T]]:
    """Decorator to inject resource authorization helpers into a class.

    The target class must expose an ``id`` attribute convertible to ``EntityId``.
    """

    ns = NamespaceId(namespace)

    def decorator(cls: type[T]) -> type[T]:
        def get_resource_ref(self: Any) -> Obj:
            return Obj(namespace=ns, id=EntityId(str(self.id)))

        # Bind methods from mixin
        cls.get_resource_ref = get_resource_ref
        cls.grant = AuthorizableResource.grant
        cls.revoke = AuthorizableResource.revoke
        cls.check = AuthorizableResource.check
        cls.who_can = AuthorizableResource.who_can
        cls.get_permissions = AuthorizableResource.get_permissions
        return cls

    return decorator


__all__ = ["authorizable_resource"]
