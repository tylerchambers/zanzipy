import types

from flask import Flask

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
            repo.write(RelationTuple.from_string("document:doc1#owner@user:alice"))

        res = client.get("/mixins/doc1/alice")
        assert res.status_code == 200
        assert res.get_json() == {"allowed": True}

        res = client.get("/mixins/doc1/bob")
        assert res.status_code == 200
        assert res.get_json() == {"allowed": False}
