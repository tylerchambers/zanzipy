"""
Document Drive: Flask app factory with SQLAlchemy storage
--------------------------------------------------------

Configure zanzipy once, then use ``current_zanzibar`` inside request handlers.
The app keeps normal domain tables in SQLAlchemy and stores authorization facts
in zanzipy's SQLAlchemy relation tables.

What to notice:
- the Flask extension builds a request-scoped ``ZanzibarEngine``;
- route handlers can call ``current_zanzibar.check`` directly;
- the lookup route exposes ``list_objects`` over HTTP;
- the seeded data includes an explicit document ban to show deny overrides.

Requires Flask and SQLAlchemy outside a checkout:
    pip install "zanzipy[flask,sqlalchemy]"

Run:
    uv run python examples/document_drive_flask_sqlalchemy.py

The script prints copy/paste environment variables. Useful routes:
    curl -s http://127.0.0.1:5000/folder/$FOLDER_ID/can_view/$BOB_ID
    curl -s http://127.0.0.1:5000/document/$DOCUMENT_ID/can_view/$CHARLIE_ID
    curl -s http://127.0.0.1:5000/user/$BOB_ID/documents/can_view
"""

from dataclasses import dataclass, field
import types
from uuid import uuid4

from flask import Flask, jsonify
from sqlalchemy import String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from zanzipy.dsl import NamespaceBuilder, SchemaBuilder
from zanzipy.integration.flask import Zanzibar
from zanzipy.integration.flask.proxy import current_zanzibar
from zanzipy.integration.mixins import (
    AuthorizableGroup,
    AuthorizableResource,
    AuthorizableSubject,
)
from zanzipy.schema import (
    ComputedUsersetRule,
    ExclusionRule,
    TupleToUsersetRule,
    UnionRule,
)
from zanzipy.storage.cache.concrete.lru import LruTupleCache
from zanzipy.storage.repos.concrete.sqlalchemy import SQLAlchemyRelationRepository

# === Domain models (SQLAlchemy) ===============================================


class Base(DeclarativeBase):
    pass


class User(Base, AuthorizableSubject):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String, nullable=False)

    def get_subject_dict(self) -> dict:
        return {"namespace": "user", "id": self.id}


class Team(Base, AuthorizableGroup):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String, nullable=False)

    # As a resource (group itself)
    def get_resource_dict(self) -> dict:
        return {"namespace": "group", "id": self.id}

    # As a subject-set (group#member)
    def get_subject_dict(self) -> dict:
        return {"namespace": "group", "id": self.id, "relation": "member"}


# === Other domain models (not persisted) ======================================


@dataclass
class Folder(AuthorizableResource):
    name: str
    id: str = field(default_factory=lambda: str(uuid4()))

    def get_resource_dict(self) -> dict:
        return {"namespace": "folder", "id": self.id}


@dataclass
class Document(AuthorizableResource):
    title: str
    id: str = field(default_factory=lambda: str(uuid4()))

    def get_resource_dict(self) -> dict:
        return {"namespace": "document", "id": self.id}


folder: Folder
document: Document


# === Zanzibar schema (DSL) ====================================================

user_ns = NamespaceBuilder("user").build()

group_ns = NamespaceBuilder("group").relation("member", subjects=["user"]).build()

folder_ns = (
    NamespaceBuilder("folder")
    .relation("owner", subjects=["user", "group#member"])
    .relation("editor", subjects=["user", "group#member"])
    .relation("viewer", subjects=["user", "group#member"])
    .relation("banned", subjects=["user"])
    .relation("parent", subjects=["folder"])  # for nested folders, unused here
    .permission_with_rewrite(
        "can_view",
        ExclusionRule(
            base=UnionRule(
                children=(
                    ComputedUsersetRule("owner"),
                    ComputedUsersetRule("editor"),
                    ComputedUsersetRule("viewer"),
                    TupleToUsersetRule(
                        tuple_relation="parent", computed_relation="viewer"
                    ),
                )
            ),
            subtract=ComputedUsersetRule("banned"),
        ),
    )
    .permission("can_edit", union=["owner", "editor"])  # shorthand union
    .build()
)

document_ns = (
    NamespaceBuilder("document")
    .relation("owner", subjects=["user", "group#member"])
    .relation("editor", subjects=["user", "group#member"])
    .relation("viewer", subjects=["user", "group#member"])
    .relation("banned", subjects=["user"])
    .relation("parent", subjects=["folder"])  # cross-namespace parent
    .permission_with_rewrite(
        "can_view",
        ExclusionRule(
            base=UnionRule(
                children=(
                    ComputedUsersetRule("owner"),
                    ComputedUsersetRule("editor"),
                    ComputedUsersetRule("viewer"),
                    TupleToUsersetRule(
                        tuple_relation="parent", computed_relation="viewer"
                    ),
                )
            ),
            subtract=ComputedUsersetRule("banned"),
        ),
    )
    .permission("can_edit", union=["owner", "editor"])  # shorthand union
    .build()
)

registry = (
    SchemaBuilder()
    .add_namespace(user_ns)
    .add_namespace(group_ns)
    .add_namespace(folder_ns)
    .add_namespace(document_ns)
    .build()
)


# === Flask app, Engine, and Repositories =====================================


def create_app() -> Flask:
    app = Flask(__name__)

    # Demo-only: StaticPool keeps one in-memory SQLite database alive across
    # sessions. Use your normal SQLAlchemy engine in a real Flask app.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    # Create domain tables
    Base.metadata.create_all(bind=engine)

    # Create authorization tables
    rel_repo = SQLAlchemyRelationRepository(SessionLocal)
    rel_repo.create_schema(engine)

    # Configure Zanzibar extension. These config values are factories on
    # purpose: production apps can build app-aware repositories and caches.
    zanzibar = Zanzibar()

    # A SimpleNamespace stands in for a schema module with a `registry` export.
    schema_module = types.SimpleNamespace(registry=registry)
    app.config["ZANZIBAR_SCHEMA"] = schema_module
    app.config["ZANZIBAR_RELATIONS_REPO"] = lambda: rel_repo
    app.config["ZANZIBAR_TUPLE_CACHE"] = lambda _app: LruTupleCache(
        max_entries=10_000, ttl_seconds=30
    )

    zanzibar.init_app(app)

    # Seed domain and tuples
    app.config["DEMO_IDS"] = seed_data(SessionLocal)

    # Routes use the request-scoped proxy, so handlers do not need to carry the
    # client or engine through every function signature.
    @app.get("/folder/<folder_id>/can_view/<user_id>")
    def folder_can_view(folder_id: str, user_id: str):  # type: ignore[override]
        allowed = current_zanzibar.check(
            f"folder:{folder_id}", "can_view", f"user:{user_id}"
        )
        return jsonify({"allowed": allowed})

    @app.get("/document/<doc_id>/can_view/<user_id>")
    def document_can_view(doc_id: str, user_id: str):  # type: ignore[override]
        allowed = current_zanzibar.check(
            f"document:{doc_id}", "can_view", f"user:{user_id}"
        )
        return jsonify({"allowed": allowed})

    @app.get("/team/<team_id>/has_member/<user_id>")
    def team_has_member(team_id: str, user_id: str):  # type: ignore[override]
        # Check group membership purely via Zanzibar tuples (no domain join)
        allowed = current_zanzibar.check(
            f"group:{team_id}", "member", f"user:{user_id}"
        )
        return jsonify({"allowed": allowed})

    @app.get("/user/<user_id>/documents/can_view")
    def user_documents_can_view(user_id: str):  # type: ignore[override]
        # Reverse lookup answers "which documents can this user view?"
        client = current_zanzibar.client
        if client is None:
            raise RuntimeError("Zanzibar client not configured")
        objects = client.list_objects("document", "can_view", f"user:{user_id}")
        return jsonify({"objects": objects})

    return app


def seed_data(SessionLocal) -> dict[str, str]:
    # Create resource instances (not persisted)
    global folder, document
    folder = Folder(name="Project")
    document = Document(title="Spec")

    # Persist domain data, then mirror only authorization facts into zanzipy.
    with SessionLocal() as db:
        alice = User(name="Alice")
        bob = User(name="Bob")
        charlie = User(name="Charlie")
        dora = User(name="Dora")
        eve = User(name="Eve")
        eng = Team(name="Engineering")

        db.add_all([alice, bob, charlie, dora, eve, eng])
        db.commit()

        eng.add_member(bob)
        eng.add_member(charlie)

        folder.grant(alice, "owner")
        folder.grant(eng, "viewer")
        document.grant(alice, "owner")
        document.grant(folder, "parent")
        document.grant(dora, "editor")
        document.grant(charlie, "banned")

        demo_ids = {
            "folder": folder.id,
            "document": document.id,
            "alice": alice.id,
            "bob": bob.id,
            "charlie": charlie.id,
            "dora": dora.id,
            "eve": eve.id,
            "eng_team": eng.id,
        }

        # Shell-friendly exports for easy copy/paste in your terminal
        print()
        print("-" * 80)
        print("# Environment variables for testing:")
        print(f"export FOLDER_ID='{demo_ids['folder']}'")
        print(f"export DOCUMENT_ID='{demo_ids['document']}'")
        print(f"export ALICE_ID='{demo_ids['alice']}'")
        print(f"export BOB_ID='{demo_ids['bob']}'")
        print(f"export CHARLIE_ID='{demo_ids['charlie']}'")
        print(f"export DORA_ID='{demo_ids['dora']}'")
        print(f"export EVE_ID='{demo_ids['eve']}'")
        print(f"export ENG_TEAM_ID='{demo_ids['eng_team']}'")
        print("-" * 80)
        print()
        # Expected checks:
        # - Bob can view via group:eng#member.
        # - Charlie is also in Engineering, but document:banned makes this False.
        # - Eve has no tuple path, so she stays denied.
        # curl -s http://127.0.0.1:5000/folder/$FOLDER_ID/can_view/$BOB_ID
        # curl -s http://127.0.0.1:5000/document/$DOCUMENT_ID/can_view/$CHARLIE_ID
        # curl -s http://127.0.0.1:5000/document/$DOCUMENT_ID/can_view/$EVE_ID
        # curl -s http://127.0.0.1:5000/user/$BOB_ID/documents/can_view

        return demo_ids


if __name__ == "__main__":
    app = create_app()
    print("=== Document Drive (Flask + SQLAlchemy + Mixins) ===")
    print("App started on http://127.0.0.1:5000")
    app.run(debug=True, use_reloader=False)
