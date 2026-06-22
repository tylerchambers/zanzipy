"""Flask helpers for zanzipy.

Provides a lightweight Flask extension to initialize a `ZanzibarClient` and
configure the request-scoped engine used by `zanzipy.integration.mixins`.

Typical usage (app factory):

    from zanzipy.integration.flask import Zanzibar

    zanzibar = Zanzibar()

    def create_app():
        app = Flask(__name__)
        # set up DB and schema imports here...
        zanzibar.init_app(app)
        return app

The extension expects your application to provide (directly or via config):
- a built schema registry module path in `ZANZIBAR_SCHEMA` with attribute `registry`
- a callable or object that yields a relations repository in `ZANZIBAR_RELATIONS_REPO`
- optional tuple cache factory in `ZANZIBAR_TUPLE_CACHE`

You can instead pass these explicitly to `init_app`.
"""

from dataclasses import dataclass
from importlib import import_module
from inspect import signature
from typing import TYPE_CHECKING, Any, cast

from zanzipy.client import ZanzibarClient
from zanzipy.engine_integration import (
    ZanzibarEngine,
    configure_authorization,
    reset_authorization,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextvars import Token

    from zanzipy.storage.cache.abstract.tuples import TupleCache


_EXTENSION_KEY = "zanzibar"
_STATE_KEY = "zanzibar_state"
_CONTEXT_HANDLERS_KEY = "zanzibar_context_handlers"
_ENGINE_TOKEN_ATTR = "_zanzipy_engine_token"


@dataclass(frozen=True, slots=True)
class _ZanzibarState:
    client: ZanzibarClient
    engine: ZanzibarEngine


class Zanzibar:
    """Flask extension that exposes a per-app Zanzibar client and engine.

    The extension object is reusable across app factories. App-specific state is
    stored on ``app.extensions`` and resolved from the active Flask context, so
    one app cannot accidentally authorize against another app's repository.
    """

    def __init__(self, app: Any | None = None) -> None:
        self._app_keys: set[int] = set()
        self._default_state: _ZanzibarState | None = None
        self._default_token: Token[ZanzibarEngine] | None = None
        if app is not None:
            self.init_app(app)

    @property
    def client(self) -> ZanzibarClient | None:
        state = self._optional_state()
        return None if state is None else state.client

    @property
    def engine(self) -> ZanzibarEngine | None:
        state = self._optional_state()
        return None if state is None else state.engine

    def init_app(
        self,
        app: Any,
        *,
        schema_registry: Any | None = None,
        relations_repository: Any | Callable[..., Any] | None = None,
        tuple_cache: Any | Callable[..., Any] | None = None,
        enable_debug: bool | None = None,
        max_check_depth: int | None = None,
    ) -> None:
        """Initialize the extension and bind it to an app.

        You can pass explicit `schema_registry`, `relations_repository`, and
        `tuple_cache` instances/factories. If omitted, they will be discovered
        from app.config using keys:
          - ZANZIBAR_SCHEMA: import path like "myapp.authz_schema:registry" or
            a module object with attribute `registry`
          - ZANZIBAR_RELATIONS_REPO: object or callable returning a repo
          - ZANZIBAR_TUPLE_CACHE: object or callable returning a cache

        Factories may accept the Flask app as their only positional argument.
        """

        self._ensure_extensions(app)

        registry = (
            schema_registry
            if schema_registry is not None
            else self._resolve_schema(app)
        )
        relations_repo = (
            relations_repository
            if relations_repository is not None
            else self._resolve_relations_repo(app)
        )
        raw_cache = (
            tuple_cache if tuple_cache is not None else self._resolve_tuple_cache(app)
        )

        relations_repo = self._materialize_config_value(relations_repo, app)
        cache = cast(
            "TupleCache | None",
            self._materialize_config_value(raw_cache, app),
        )

        client = ZanzibarClient(
            schema=registry,
            relations_repository=relations_repo,
            enable_debug=bool(
                app.config.get(
                    "ZANZIBAR_DEBUG",
                    False if enable_debug is None else enable_debug,
                )
            ),
            max_check_depth=int(
                app.config.get(
                    "ZANZIBAR_MAX_DEPTH",
                    25 if max_check_depth is None else max_check_depth,
                )
            ),
            tuple_cache=cache,
        )
        state = _ZanzibarState(client=client, engine=ZanzibarEngine(client))

        app.extensions[_EXTENSION_KEY] = self
        app.extensions[_STATE_KEY] = state
        self._app_keys.add(id(app))
        self._default_state = state if len(self._app_keys) == 1 else None
        self._bind_default_engine()
        self._install_context_binding(app, state)

    def check(self, *args: Any, **kwargs: Any) -> bool:
        return self._require_state().client.check(*args, **kwargs)

    def write(self, *args: Any, **kwargs: Any) -> None:
        self._require_state().client.write(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> bool:
        return self._require_state().client.delete(*args, **kwargs)

    def expand(self, *args: Any, **kwargs: Any) -> Any:
        return self._require_state().client.expand(*args, **kwargs)

    def _require_state(self) -> _ZanzibarState:
        state = self._optional_state()
        if state is None:
            raise RuntimeError("Zanzibar extension is not initialized")
        return state

    def _optional_state(self) -> _ZanzibarState | None:
        current_state = self._current_app_state()
        if current_state is not None:
            return current_state
        return self._default_state

    def _current_app_state(self) -> _ZanzibarState | None:
        try:
            from flask import current_app, has_app_context
        except Exception:
            return None

        if not has_app_context():
            return None

        if current_app.extensions.get(_EXTENSION_KEY) is not self:
            raise RuntimeError("Zanzibar extension not initialized on this app")
        state = current_app.extensions.get(_STATE_KEY)
        if state is None:
            raise RuntimeError("Zanzibar extension state missing on this app")
        return cast("_ZanzibarState", state)

    def _bind_default_engine(self) -> None:
        if self._default_token is not None:
            reset_authorization(self._default_token)
            self._default_token = None
        if self._default_state is not None:
            self._default_token = configure_authorization(self._default_state.engine)

    def _install_context_binding(self, app: Any, state: _ZanzibarState) -> None:
        try:
            from flask import appcontext_pushed, appcontext_tearing_down, g
        except Exception:
            if hasattr(app, "before_request"):
                app.before_request(lambda: configure_authorization(state.engine))
            return

        old_handlers = app.extensions.get(_CONTEXT_HANDLERS_KEY)
        if old_handlers is not None:
            pushed_handler, popped_handler = old_handlers
            appcontext_pushed.disconnect(pushed_handler, app)
            appcontext_tearing_down.disconnect(popped_handler, app)

        def bind_engine(sender: Any, **_: Any) -> None:
            if sender is app:
                setattr(g, _ENGINE_TOKEN_ATTR, configure_authorization(state.engine))

        def reset_engine(sender: Any, **_: Any) -> None:
            if sender is not app:
                return
            token = getattr(g, _ENGINE_TOKEN_ATTR, None)
            if token is None:
                return
            reset_authorization(token)
            delattr(g, _ENGINE_TOKEN_ATTR)

        appcontext_pushed.connect(bind_engine, app, weak=False)
        appcontext_tearing_down.connect(reset_engine, app, weak=False)
        app.extensions[_CONTEXT_HANDLERS_KEY] = (bind_engine, reset_engine)

    @staticmethod
    def _ensure_extensions(app: Any) -> None:
        if not hasattr(app, "extensions"):
            app.extensions = {}

    def _resolve_schema(self, app: Any) -> Any:
        value = app.config.get("ZANZIBAR_SCHEMA")
        if value is None:
            raise RuntimeError(
                "ZANZIBAR_SCHEMA not provided. Pass schema_registry to init_app "
                "or set app.config['ZANZIBAR_SCHEMA']."
            )
        if isinstance(value, str):
            module_path, _, attr = value.partition(":")
            module = import_module(module_path)
            return getattr(module, attr or "registry")
        return getattr(value, "registry", value)

    def _resolve_relations_repo(self, app: Any) -> Any:
        value = app.config.get("ZANZIBAR_RELATIONS_REPO")
        if value is None:
            raise RuntimeError(
                "ZANZIBAR_RELATIONS_REPO not provided. Provide an instance or factory."
            )
        return value

    def _resolve_tuple_cache(self, app: Any) -> Any:
        return app.config.get("ZANZIBAR_TUPLE_CACHE")

    def _materialize_config_value(self, value: Any, app: Any) -> Any:
        if not callable(value):
            return value
        return self._call_factory(value, app)

    def _call_factory(self, factory: Any, app: Any) -> Any:
        try:
            factory_signature = signature(factory)
        except (TypeError, ValueError):
            return factory(app)

        try:
            factory_signature.bind(app)
        except TypeError:
            return factory()
        return factory(app)
