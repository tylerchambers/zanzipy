"""Flask view decorators for Zanzibar authorization checks."""

from functools import wraps
from typing import TYPE_CHECKING, Any, Protocol

from .proxy import current_zanzibar

if TYPE_CHECKING:
    from collections.abc import Callable


class SubjectResolver(Protocol):
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

    Args:
        object_resolver: function receiving view args/kwargs and returning
            an object string like 'document:abc'.
        relation: permission or relation to check.
        subject_resolver: function receiving view args/kwargs and returning
            a subject string like 'user:123'.
        on_denied: optional callable returning a Flask response when denied.
            If not provided, returns (403, {'allowed': False}).
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
