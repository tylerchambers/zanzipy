"""Flask view decorators for Zanzibar authorization checks."""

from functools import wraps
from typing import TYPE_CHECKING, Any, Protocol

from .proxy import current_zanzibar

if TYPE_CHECKING:
    from collections.abc import Callable


class SubjectResolver(Protocol):
    """Protocol for callables that resolve the current request subject."""

    def __call__(self, *args: Any, **kwargs: Any) -> str:
        """Return a canonical subject string like 'user:123'."""


def permission_required(
    object_resolver: Callable[..., str],
    relation: str,
    subject_resolver: SubjectResolver,
    *,
    on_denied: Callable[[], Any] | None = None,
):
    """Guard a Flask view with a Zanzibar permission check.

    The resolvers receive the same arguments as the wrapped view, and the
    check is delegated through the current Flask extension proxy.

    Args:
        object_resolver: Function returning an object string like 'document:abc'.
        relation: Permission or relation to check.
        subject_resolver: Function returning a subject string like 'user:123'.
        on_denied: Optional callable returning a Flask response when denied.
    """

    def decorator(view_func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view_func)
        def wrapper(*args: Any, **kwargs: Any):
            obj = object_resolver(*args, **kwargs)
            subj = subject_resolver(*args, **kwargs)
            allowed = current_zanzibar.check(obj, relation, subj)
            if not allowed:
                if on_denied is not None:
                    return on_denied()
                # default: JSON-ish 403; avoid importing Flask here
                return ({"allowed": False}, 403)
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


__all__ = ["permission_required"]
