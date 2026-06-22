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

from typing import TYPE_CHECKING, Any, cast

from zanzipy.client import ZanzibarClient
from zanzipy.engine_integration import ZanzibarEngine, configure_authorization

if TYPE_CHECKING:
    from collections.abc import Callable

    from zanzipy.storage.cache.abstract.tuples import TupleCache


class Zanzibar:
    """Flask extension that owns a singleton ZanzibarClient and Engine.

    The engine is injected into request context at each request using the
    existing contextvar-based integration, so mixins work inside views and
    other request code.
    """

    def __init__(self, app: Any | None = None) -> None:
        self.client: ZanzibarClient | None = None
        self.engine: ZanzibarEngine | None = None
        if app is not None:
            self.init_app(app)

    def init_app(
        self,
        app: Any,
        *,
        schema_registry: Any | None = None,
        relations_repository: Any | Callable[[], Any] | None = None,
        tuple_cache: Any | Callable[[Any], Any] | None = None,
        enable_debug: bool | None = None,
        max_check_depth: int | None = None,
    ) -> None:
        """Initialize the extension and bind it to an app.

        You can pass explicit `schema_registry`, `relations_repository`, and
        `tuple_cache` instances/factories. If omitted, they will be discovered
        from app.config using keys:
          - ZANZIBAR_SCHEMA: import path like "myapp.authz_schema:registry" or
            a module object with attribute `registry`
          - ZANZIBAR_RELATIONS_REPO: object or zero-arg callable returning a repo
          - ZANZIBAR_TUPLE_CACHE: object or callable(app) -> cache
        """

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

        if callable(relations_repo):
            relations_repo = relations_repo()
        # Support cache factories accepting 0 or 1 argument (app)
        if callable(raw_cache):
            try:
                produced_cache = raw_cache(app)
            except TypeError:
                produced_cache = raw_cache()
        else:
            produced_cache = raw_cache
        cache = cast("TupleCache | None", produced_cache)

        client = ZanzibarClient(
            schema=registry,
            relations_repository=relations_repo,
            enable_debug=bool(app.config.get("ZANZIBAR_DEBUG", enable_debug or False)),
            max_check_depth=int(
                app.config.get("ZANZIBAR_MAX_DEPTH", max_check_depth or 25)
            ),
            tuple_cache=cache,
        )

        engine = ZanzibarEngine(client)

        # Store on app extensions and set up request hook to configure engine
        if not hasattr(app, "extensions"):
            app.extensions = {}
        app.extensions["zanzibar"] = self

        self.client = client
        self.engine = engine

        # Configure the engine immediately (for app-level operations) and also
        # re-configure per request to ensure correct context in async or nested flows.
        configure_authorization(engine)

        # Register a before_request hook without importing Flask directly to
        # avoid adding a hard dependency. We rely on app's provided API.
        if hasattr(app, "before_request"):
            app.before_request(lambda: configure_authorization(engine))

    # Convenience proxies to client API
    def check(self, *args: Any, **kwargs: Any) -> bool:
        assert self.client is not None
        return self.client.check(*args, **kwargs)

    def write(self, *args: Any, **kwargs: Any) -> None:
        assert self.client is not None
        self.client.write(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> bool:
        assert self.client is not None
        return self.client.delete(*args, **kwargs)

    def expand(self, *args: Any, **kwargs: Any) -> Any:
        assert self.client is not None
        return self.client.expand(*args, **kwargs)

    # Resolvers (private helpers)
    def _resolve_schema(self, app: Any) -> Any:
        value = app.config.get("ZANZIBAR_SCHEMA")
        if value is None:
            raise RuntimeError(
                "ZANZIBAR_SCHEMA not provided. Pass schema_registry to init_app "
                "or set app.config['ZANZIBAR_SCHEMA']."
            )
        if isinstance(value, str):
            module_path, _, attr = value.partition(":")
            if not attr:
                attr = "registry"
            mod = __import__(module_path, fromlist=[attr])
            return getattr(mod, attr)
        # assume module with `registry` attribute or the registry itself
        return getattr(value, "registry", value)

    def _resolve_relations_repo(self, app: Any) -> Any:
        value = app.config.get("ZANZIBAR_RELATIONS_REPO")
        if value is None:
            raise RuntimeError(
                "ZANZIBAR_RELATIONS_REPO not provided. Provide an instance or factory."
            )
        return value

    def _resolve_tuple_cache(self, app: Any) -> Any:
        value = app.config.get("ZANZIBAR_TUPLE_CACHE")
        if value is None:
            # No cache by default
            return None
        return value
