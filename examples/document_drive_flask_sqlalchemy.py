"""
Document Drive (Flask + SQLAlchemy + Mixins)
-----------------------------------------

This example mirrors document_drive_sqlalchemy_and_mixins.py but demonstrates the
Flask integration and request-scoped engine binding. It uses:
 - SQLAlchemy for domain persistence (users, teams)
 - zanzipy mixins for domain-friendly authorization helpers
 - the Flask extension to initialize a ZanzibarClient and bind the engine

Run:
    uv run python examples/document_drive_flask_sqlalchemy.py

Then visit:
    http://127.0.0.1:5000/folder/<folder_id>/can_view/<user_id>
    http://127.0.0.1:5000/document/<doc_id>/can_view/<user_id>
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

    # In-memory SQLite DB for demo
    # Use a single shared in-memory SQLite connection across sessions
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
    rel_repo._metadata.create_all(bind=engine)

    # Configure Zanzibar extension
    zanzibar = Zanzibar()

    # Provide schema via module-like object with attribute `registry`
    schema_module = types.SimpleNamespace(registry=registry)
    app.config["ZANZIBAR_SCHEMA"] = schema_module
    # Provide repo as a factory (here we reuse the prepared instance)
    app.config["ZANZIBAR_RELATIONS_REPO"] = lambda: rel_repo
    # Optional: in-process cache for hot reads
    app.config["ZANZIBAR_TUPLE_CACHE"] = lambda _app: LruTupleCache(
        max_entries=10_000, ttl_seconds=30
    )

    zanzibar.init_app(app)

    # Seed domain and tuples
    seed_data(SessionLocal)

    # Routes using proxy and/or mixins
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

    return app


def seed_data(SessionLocal) -> None:
    # Create resource instances (not persisted)
    global folder, document
    folder = Folder(name="Project")
    document = Document(title="Spec")

    # Persist users/teams and membership, then mirror to auth tuples via mixins
    with SessionLocal() as db:
        alice = User(name="Alice")
        bob = User(name="Bob")
        charlie = User(name="Charlie")
        dora = User(name="Dora")
        eve = User(name="Eve")
        eng = Team(name="Engineering")

        db.add_all([alice, bob, charlie, dora, eve, eng])
        db.commit()

        # Create group membership via authorization tuples (no domain join table)
        eng.add_member(bob)
        eng.add_member(charlie)

        # Share folder/document
        folder.grant(alice, "owner")
        folder.grant(eng, "viewer")
        document.grant(alice, "owner")
        document.grant(folder, "parent")
        document.grant(dora, "editor")

        # Shell-friendly exports for easy copy/paste in your terminal
        print()
        print("-" * 80)
        print("# Environment variables for testing:")
        print(f"export FOLDER_ID='{folder.id}'")
        print(f"export DOCUMENT_ID='{document.id}'")
        print(f"export ALICE_ID='{alice.id}'")
        print(f"export BOB_ID='{bob.id}'")
        print(f"export CHARLIE_ID='{charlie.id}'")
        print(f"export DORA_ID='{dora.id}'")
        print(f"export EVE_ID='{eve.id}'")
        print(f"export ENG_TEAM_ID='{eng.id}'")
        print("-" * 80)
        print()
        # Then to test, you can run curl commands like:
        # curl -s http://127.0.0.1:5000/folder/$FOLDER_ID/can_view/$ALICE_ID
        # curl -s http://127.0.0.1:5000/folder/$FOLDER_ID/can_view/$BOB_ID
        # curl -s http://127.0.0.1:5000/document/$DOCUMENT_ID/can_view/$ALICE_ID
        # curl -s http://127.0.0.1:5000/document/$DOCUMENT_ID/can_view/$BOB_ID
        # curl -s http://127.0.0.1:5000/team/$ENG_TEAM_ID/has_member/$ALICE_ID
        # curl -s http://127.0.0.1:5000/team/$ENG_TEAM_ID/has_member/$BOB_ID


if __name__ == "__main__":
    app = create_app()
    print("=== Document Drive (Flask + SQLAlchemy + Mixins) ===")
    print("App started on http://127.0.0.1:5000")
    app.run(debug=True)
