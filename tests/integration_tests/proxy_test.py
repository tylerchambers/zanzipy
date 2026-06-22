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
