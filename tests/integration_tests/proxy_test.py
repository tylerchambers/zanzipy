import builtins
import types

from flask import Flask
import pytest

from zanzipy.integration.flask import Zanzibar
from zanzipy.integration.flask.proxy import current_zanzibar
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.repos.concrete.memory.relations import (
    InMemoryRelationRepository,
)


def _registry() -> SchemaRegistry:
    reg = SchemaRegistry()
    reg.register(
        NamespaceDef(
            name="doc",
            relations=(
                RelationDef.with_subjects(
                    "owner", (SubjectReference.from_dict({"namespace": "user"}),)
                ),
            ),
            permissions=(
                PermissionDef(name="can_view", rewrite=ComputedUsersetRule("owner")),
            ),
        )
    )
    return reg


class TestFlaskProxy:
    def _app(self) -> Flask:
        app = Flask(__name__)
        schema_module = types.SimpleNamespace(registry=_registry())
        app.config["ZANZIBAR_SCHEMA"] = schema_module
        app.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
        Zanzibar().init_app(app)
        return app

    def test_proxy_requires_app_context(self) -> None:
        # Accessing without app/request context should raise
        with pytest.raises(RuntimeError):
            _ = current_zanzibar.client  # type: ignore[attr-defined]

    def test_proxy_resolves_extension(self) -> None:
        app = self._app()
        with app.app_context():
            # Extension should be found and expose client
            assert current_zanzibar.client is app.extensions["zanzibar"].client  # type: ignore[attr-defined]

    def test_proxy_call_invokes_resolved_extension(self) -> None:
        app = Flask("proxy-call")

        class CallableExtension:
            def __call__(self, value: str, *, suffix: str) -> str:
                return f"{value}-{suffix}"

        app.extensions["zanzibar"] = CallableExtension()

        with app.app_context():
            assert current_zanzibar("ping", suffix="pong") == "ping-pong"

    def test_proxy_reports_missing_extension_on_current_app(self) -> None:
        app = Flask("proxy-missing-extension")

        with (
            app.app_context(),
            pytest.raises(RuntimeError, match="Zanzibar extension not initialized"),
        ):
            _ = current_zanzibar.client  # type: ignore[attr-defined]

    def test_find_extension_wraps_flask_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import zanzipy.integration.flask.proxy as proxy

        real_import = builtins.__import__

        def import_without_flask(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "flask":
                raise ImportError("blocked flask import")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", import_without_flask)

        with pytest.raises(RuntimeError, match="Flask is not installed"):
            proxy._find_extension()
