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
