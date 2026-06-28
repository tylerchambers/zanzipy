import sys
import types

from flask import Flask
import pytest

from zanzipy.client import ZanzibarClient
from zanzipy.integration.flask import Zanzibar
from zanzipy.integration.flask.proxy import current_zanzibar
from zanzipy.models.tuple import RelationTuple
from zanzipy.schema.namespace import NamespaceDef
from zanzipy.schema.permissions import PermissionDef
from zanzipy.schema.registry import SchemaRegistry
from zanzipy.schema.relations import RelationDef
from zanzipy.schema.rules import ComputedUsersetRule
from zanzipy.schema.subjects import SubjectReference
from zanzipy.storage.repos.concrete.memory.relations import (
    InMemoryRelationRepository,
)
from zanzipy.storage.revision import TenantId, TupleMutation, WriteContext

DEFAULT_TENANT = TenantId("default")


def _make_registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    ns = NamespaceDef(
        name="document",
        relations=(
            RelationDef.with_subjects(
                "owner", (SubjectReference.from_dict({"namespace": "user"}),)
            ),
            RelationDef.with_subjects(
                "viewer",
                (SubjectReference.from_dict({"namespace": "user"}),),
                rewrite=ComputedUsersetRule("owner"),
            ),
        ),
        permissions=(
            PermissionDef(name="can_view", rewrite=ComputedUsersetRule("viewer")),
        ),
    )
    registry.register(ns)
    return registry


class TestFlaskZanzibarExtension:
    def _create_app(self) -> Flask:
        app = Flask(__name__)
        registry = _make_registry()

        # Provide config entries the extension expects
        # schema via module-like object with attribute `registry`
        schema_module = types.SimpleNamespace(registry=registry)
        app.config["ZANZIBAR_SCHEMA"] = schema_module
        app.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository

        # Initialize extension
        ext = Zanzibar()
        ext.init_app(app)
        return app

    def test_client_is_initialized_and_registered(self) -> None:
        app = self._create_app()

        with app.app_context():
            ext = app.extensions.get("zanzibar")
            assert isinstance(ext, Zanzibar)
            assert isinstance(ext.client, ZanzibarClient)

    def test_configured_tenant_is_passed_to_client(self) -> None:
        app = Flask("tenant-config")
        app.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(registry=_make_registry())
        app.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
        app.config["ZANZIBAR_TENANT"] = "acme"

        ext = Zanzibar()
        ext.init_app(app)

        with app.app_context():
            assert ext.client is not None
            assert ext.client.tenant == TenantId("acme")

    def test_max_depth_argument_and_config_are_passed_to_client(self) -> None:
        app = Flask("depth-argument")
        app.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(registry=_make_registry())
        app.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository

        ext = Zanzibar()
        ext.init_app(app, max_depth=7)

        with app.app_context():
            assert ext.client is not None
            assert ext.client.max_depth == 7

        configured_app = Flask("depth-config")
        configured_app.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
            registry=_make_registry()
        )
        configured_app.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
        configured_app.config["ZANZIBAR_MAX_DEPTH"] = 3

        configured_ext = Zanzibar()
        configured_ext.init_app(configured_app, max_depth=7)

        with configured_app.app_context():
            assert configured_ext.client is not None
            assert configured_ext.client.max_depth == 3

    def test_constructor_accepts_app_and_delegates_delete_and_expand(self) -> None:
        app = Flask("constructor-app")
        app.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(registry=_make_registry())
        app.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository

        ext = Zanzibar(app)

        with app.app_context():
            ext.write("document:doc1", "owner", "user:alice")
            expanded = ext.expand("document:doc1", "can_view")
            assert expanded.users == {"user:alice"}

            ext.delete("document:doc1", "owner", "user:alice")

            assert ext.check("document:doc1", "can_view", "user:alice") is False

    def test_context_binding_without_flask_signals_uses_before_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import builtins

        import zanzipy.integration.flask as flask_integration

        class _FallbackApp:
            def __init__(self) -> None:
                self.extensions: dict[str, object] = {}
                self.callbacks: list[object] = []

            def before_request(self, callback: object) -> None:
                self.callbacks.append(callback)

        real_import = builtins.__import__

        def fail_flask_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "flask":
                raise ImportError("flask unavailable")
            return real_import(name, *args, **kwargs)

        client = ZanzibarClient(
            relations_repository=InMemoryRelationRepository(),
            schema=_make_registry(),
        )
        state = flask_integration._ZanzibarState(
            client=client,
            engine=flask_integration.ZanzibarEngine(client),
        )
        app = _FallbackApp()
        monkeypatch.setattr(builtins, "__import__", fail_flask_import)

        Zanzibar()._install_context_binding(app, state)

        assert len(app.callbacks) == 1

    def test_context_teardown_ignores_other_apps_and_missing_tokens(self) -> None:
        from flask import g

        from zanzipy.integration.flask import (
            _CONTEXT_HANDLERS_KEY,
            _ENGINE_TOKEN_ATTR,
        )

        app = self._create_app()
        other_app = Flask("other-context-sender")
        _pushed_handler, popped_handler = app.extensions[_CONTEXT_HANDLERS_KEY]

        with app.app_context():
            popped_handler(other_app)
            if hasattr(g, _ENGINE_TOKEN_ATTR):
                delattr(g, _ENGINE_TOKEN_ATTR)
            popped_handler(app)

            assert not hasattr(g, _ENGINE_TOKEN_ATTR)

    def test_write_and_check_via_proxy(self) -> None:
        app = self._create_app()

        @app.route("/check/<doc_id>/<user_id>")
        def check(doc_id: str, user_id: str):  # type: ignore[override]
            allowed = current_zanzibar.check(
                f"document:{doc_id}", "can_view", f"user:{user_id}"
            )
            return {"allowed": allowed}

        client = app.test_client()
        # Seed relation: owner -> viewer -> can_view
        with app.app_context():
            ext = app.extensions["zanzibar"]
            ext.write("document:doc1", "owner", "user:alice")

        res = client.get("/check/doc1/alice")
        assert res.status_code == 200
        assert res.get_json() == {"allowed": True}

        res = client.get("/check/doc1/bob")
        assert res.status_code == 200
        assert res.get_json() == {"allowed": False}

    def test_before_request_binds_engine_for_mixins(self) -> None:
        # Even though extension exposes the client, the engine is configured for mixins
        from zanzipy.integration.mixins import AuthorizableResource, AuthorizableSubject

        class _Doc(AuthorizableResource):
            def __init__(self, id: str) -> None:
                self.id = id

            def get_resource_dict(self) -> dict:
                return {"namespace": "document", "id": self.id}

        class _User(AuthorizableSubject):
            def __init__(self, id: str) -> None:
                self.id = id

            def get_subject_dict(self) -> dict:
                return {"namespace": "user", "id": self.id}

        app = self._create_app()

        @app.route("/mixins/<doc_id>/<user_id>")
        def check_with_mixins(doc_id: str, user_id: str):  # type: ignore[override]
            d = _Doc(doc_id)
            u = _User(user_id)
            return {"allowed": d.check(u, "can_view")}

        client = app.test_client()

        # Seed tuples directly using repo to ensure mixins read via engine
        with app.app_context():
            ext = app.extensions["zanzibar"]
            repo = ext.client.relations_repository  # type: ignore[assignment]
            repo.write(
                WriteContext(DEFAULT_TENANT),
                (
                    TupleMutation.touch(
                        RelationTuple.from_string("document:doc1#owner@user:alice")
                    ),
                ),
            )

        res = client.get("/mixins/doc1/alice")
        assert res.status_code == 200
        assert res.get_json() == {"allowed": True}

        res = client.get("/mixins/doc1/bob")
        assert res.status_code == 200
        assert res.get_json() == {"allowed": False}

    def test_reusable_extension_resolves_current_app_client(self) -> None:
        zanzibar = Zanzibar()
        app1 = Flask("app1")
        app1.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
            registry=_make_registry()
        )
        app1.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
        zanzibar.init_app(app1)

        app2 = Flask("app2")
        app2.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
            registry=_make_registry()
        )
        app2.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
        zanzibar.init_app(app2)

        with app1.app_context():
            current_zanzibar.write("document:doc1", "owner", "user:alice")
            assert current_zanzibar.check("document:doc1", "can_view", "user:alice")

        with app2.app_context():
            assert not current_zanzibar.check("document:doc1", "can_view", "user:alice")
            current_zanzibar.write("document:doc1", "owner", "user:bob")

        with app1.app_context():
            assert current_zanzibar.check("document:doc1", "can_view", "user:alice")
            assert not current_zanzibar.check("document:doc1", "can_view", "user:bob")

    def test_app_context_binds_mixins_to_current_app(self) -> None:
        from zanzipy.integration.mixins import AuthorizableResource, AuthorizableSubject

        class _Doc(AuthorizableResource):
            def __init__(self, id: str) -> None:
                self.id = id

            def get_resource_dict(self) -> dict:
                return {"namespace": "document", "id": self.id}

        class _User(AuthorizableSubject):
            def __init__(self, id: str) -> None:
                self.id = id

            def get_subject_dict(self) -> dict:
                return {"namespace": "user", "id": self.id}

        zanzibar = Zanzibar()
        app1 = Flask("mixin-app1")
        app1.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
            registry=_make_registry()
        )
        app1.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
        zanzibar.init_app(app1)

        app2 = Flask("mixin-app2")
        app2.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
            registry=_make_registry()
        )
        app2.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
        zanzibar.init_app(app2)

        with app1.app_context():
            _Doc("doc1").grant(_User("alice"), "owner")

        with app2.app_context():
            assert not _Doc("doc1").check(_User("alice"), "can_view")

        with app1.app_context():
            assert _Doc("doc1").check(_User("alice"), "can_view")

    def test_tuple_cache_factory_type_error_is_not_retried(self) -> None:
        app = Flask("cache-factory")
        app.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(registry=_make_registry())
        app.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository

        calls = 0

        def broken_cache(_app: Flask):
            nonlocal calls
            calls += 1
            raise TypeError("cache boom")

        app.config["ZANZIBAR_TUPLE_CACHE"] = broken_cache

        with pytest.raises(TypeError, match="cache boom"):
            Zanzibar().init_app(app)
        assert calls == 1

    def test_missing_schema_config_raises_resolution_error(self) -> None:
        app = Flask("missing-schema")
        app.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository

        with pytest.raises(RuntimeError, match="ZANZIBAR_SCHEMA not provided"):
            Zanzibar().init_app(app)

    def test_missing_relations_repo_config_raises_resolution_error(self) -> None:
        app = Flask("missing-repo")
        app.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(registry=_make_registry())

        with pytest.raises(RuntimeError, match="ZANZIBAR_RELATIONS_REPO not provided"):
            Zanzibar().init_app(app)

    def test_string_schema_path_resolves_registry_attribute(self) -> None:
        app = Flask("schema-path")
        registry = _make_registry()
        module_name = "zanzipy_test_schema_module"
        sys.modules[module_name] = types.SimpleNamespace(registry=registry)
        app.config["ZANZIBAR_SCHEMA"] = f"{module_name}:registry"
        app.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository

        try:
            ext = Zanzibar()
            ext.init_app(app)
        finally:
            sys.modules.pop(module_name, None)

        with app.app_context():
            assert ext.client is not None
            assert ext.client.schema is registry

    def test_relation_repo_factories_use_supported_call_signatures(self) -> None:
        app_factory_arg = Flask("repo-factory-app-arg")
        app_factory_arg.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
            registry=_make_registry()
        )
        app_factory_arg_calls = []

        def repo_factory_with_app(app: Flask) -> InMemoryRelationRepository:
            app_factory_arg_calls.append(app)
            return InMemoryRelationRepository()

        app_factory_arg.config["ZANZIBAR_RELATIONS_REPO"] = repo_factory_with_app
        Zanzibar().init_app(app_factory_arg)

        assert app_factory_arg_calls == [app_factory_arg]

        no_arg_app = Flask("repo-factory-no-arg")
        no_arg_app.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
            registry=_make_registry()
        )
        no_arg_calls = []

        def repo_factory_without_args() -> InMemoryRelationRepository:
            no_arg_calls.append("called")
            return InMemoryRelationRepository()

        no_arg_app.config["ZANZIBAR_RELATIONS_REPO"] = repo_factory_without_args
        Zanzibar().init_app(no_arg_app)

        assert no_arg_calls == ["called"]

    def test_opaque_signature_factory_falls_back_to_app_argument(self) -> None:
        class OpaqueRepoFactory:
            def __init__(self) -> None:
                self.seen_app = None

            @property
            def __signature__(self) -> object:
                raise ValueError("opaque signature")

            def __call__(self, app: Flask) -> InMemoryRelationRepository:
                self.seen_app = app
                return InMemoryRelationRepository()

        app = Flask("opaque-repo-factory")
        app.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(registry=_make_registry())
        factory = OpaqueRepoFactory()
        app.config["ZANZIBAR_RELATIONS_REPO"] = factory

        Zanzibar().init_app(app)

        assert factory.seen_app is app

    def test_reinit_replaces_existing_context_handlers(self) -> None:
        from zanzipy.integration.flask import _CONTEXT_HANDLERS_KEY

        app = self._create_app()
        first_handlers = app.extensions[_CONTEXT_HANDLERS_KEY]

        app.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(registry=_make_registry())
        app.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
        app.extensions["zanzibar"].init_app(app)

        assert app.extensions[_CONTEXT_HANDLERS_KEY] != first_handlers

    def test_extension_rejects_different_current_app(self) -> None:
        zanzibar1 = Zanzibar()
        app1 = Flask("mismatch-app-1")
        app1.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
            registry=_make_registry()
        )
        app1.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
        zanzibar1.init_app(app1)

        zanzibar2 = Zanzibar()
        app2 = Flask("mismatch-app-2")
        app2.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
            registry=_make_registry()
        )
        app2.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
        zanzibar2.init_app(app2)

        with (
            app2.app_context(),
            pytest.raises(RuntimeError, match="not initialized on this app"),
        ):
            _ = zanzibar1.client

    def test_extension_reports_missing_current_app_state(self) -> None:
        from zanzipy.integration.flask import _STATE_KEY

        app = self._create_app()
        ext = app.extensions["zanzibar"]
        app.extensions.pop(_STATE_KEY)

        with app.app_context(), pytest.raises(RuntimeError, match="state missing"):
            _ = ext.client

    def test_second_app_resets_single_app_default_engine(self) -> None:
        from zanzipy.engine_integration import (
            ZanzibarEngine,
            configure_authorization,
            get_authorization_engine,
            reset_authorization,
        )

        previous_engine = ZanzibarEngine(
            ZanzibarClient(
                relations_repository=InMemoryRelationRepository(),
                schema=_make_registry(),
            )
        )
        token = configure_authorization(previous_engine)

        try:
            zanzibar = Zanzibar()
            app1 = Flask("default-reset-1")
            app1.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
                registry=_make_registry()
            )
            app1.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
            zanzibar.init_app(app1)

            assert get_authorization_engine() is zanzibar.engine

            app2 = Flask("default-reset-2")
            app2.config["ZANZIBAR_SCHEMA"] = types.SimpleNamespace(
                registry=_make_registry()
            )
            app2.config["ZANZIBAR_RELATIONS_REPO"] = InMemoryRelationRepository
            zanzibar.init_app(app2)

            assert get_authorization_engine() is previous_engine
            assert zanzibar.engine is None
        finally:
            reset_authorization(token)
