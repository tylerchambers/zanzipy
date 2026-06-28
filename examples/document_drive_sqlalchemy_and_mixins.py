"""
Document Drive: SQLAlchemy plus authorization mixins
---------------------------------------------------

This version keeps authorization verbs on your domain objects. Instead of
hand-formatting tuple strings everywhere, resources expose ``grant``/``check``
and subjects expose ``get_accessible`` through zanzipy mixins.

What to notice:
- ``Team`` is both a group resource and a ``group#member`` subject set;
- ``folder.grant(eng, "viewer")`` shares with the whole team;
- ``document.grant(folder, "parent")`` enables folder viewer inheritance;
- ``document.grant(charlie, "banned")`` proves explicit deny overrides access.

Requires SQLAlchemy outside a checkout:
    pip install "zanzipy[sqlalchemy]"

Run:
    uv run python examples/document_drive_sqlalchemy_and_mixins.py
"""

from dataclasses import dataclass, field
from uuid import uuid4

from sqlalchemy import (
    Column,
    ForeignKey,
    String,
    Table,
    create_engine,
    insert,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from zanzipy.client import ZanzibarClient
from zanzipy.dsl import NamespaceBuilder, SchemaBuilder
from zanzipy.engine_integration import (
    ZanzibarEngine,
    configure_authorization,
)
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


# Association table: team membership (domain persistence)
team_members = Table(
    "team_members",
    Base.metadata,
    Column("team_id", String, ForeignKey("teams.id"), primary_key=True),
    Column("user_id", String, ForeignKey("users.id"), primary_key=True),
)


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


# === Engine, Session, and Repositories =======================================

engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# Create domain tables
Base.metadata.create_all(bind=engine)

# Create authorization tables
rel_repo = SQLAlchemyRelationRepository(SessionLocal)
rel_repo.create_schema(engine)

# Optional: enable an in-memory LRU cache for hot relation tuple reads.
tuple_cache = LruTupleCache(max_entries=10_000, ttl_seconds=30)

client = ZanzibarClient(
    schema=registry,
    relations_repository=rel_repo,
    tuple_cache=tuple_cache,
)

# Wrap client in engine and configure for mixins
configure_authorization(ZanzibarEngine(client))


# Keep the setup explicit: domain objects are normal Python/SQLAlchemy objects,
# and only authorization facts are written through the mixin methods below.

# === Seed domain and tuples ===================================================


# Create domain instances up-front for clarity
alice = User(name="Alice")
bob = User(name="Bob")
charlie = User(name="Charlie")
dora = User(name="Dora")
eve = User(name="Eve")
eng = Team(name="Engineering")

# Create resource instances (not persisted)
folder = Folder(name="Project")
document = Document(title="Spec")

# Persist users/teams and membership
with SessionLocal() as db:
    db.add_all([alice, bob, charlie, dora, eve, eng])
    db.commit()

    db.execute(
        insert(team_members).values(
            [
                {"team_id": eng.id, "user_id": bob.id},
                {"team_id": eng.id, "user_id": charlie.id},
            ]
        )
    )
    db.commit()

    # Keep the domain join for product features, then mirror membership into
    # zanzipy tuples so authorization can follow group#member subject sets.
    eng.add_member(bob)
    eng.add_member(charlie)

# Share folder/document relationships through domain-friendly verbs. These
# calls write the same relation tuples as the lower-level client examples.
folder.grant(alice, "owner")
folder.grant(eng, "viewer")
document.grant(alice, "owner")
document.grant(folder, "parent")
document.grant(dora, "editor")
document.grant(charlie, "banned")


# === Demo checks and expansion ===============================================


print("=== Document Drive (SQLAlchemy + Mixins) ===")
print("Folder viewing:")
print(f"- Alice: {folder.check(alice, 'can_view')}")
print(f"- Bob: {folder.check(bob, 'can_view')}")
print(f"- Eve: {folder.check(eve, 'can_view')}")

print("Document viewing:")
print(f"- Alice: {document.check(alice, 'can_view')}")
print(f"- Bob: {document.check(bob, 'can_view')}")
print(
    "- Charlie (eng member, but document-banned): "
    f"{document.check(charlie, 'can_view')}"
)
print(f"- Dora: {document.check(dora, 'can_view')}")
print(f"- Eve: {document.check(eve, 'can_view')}")

# Warm both cache paths. Object reads serve check/expand; subject buckets serve
# reverse lookup through get_accessible.
_ = document.check(bob, "can_view")
_ = document.check(bob, "can_view")
_ = document.who_can("can_view")
bob_docs = bob.get_accessible("document", "can_view")
bob_docs = bob.get_accessible("document", "can_view")
print(f"Documents Bob can view via lookup: {[str(obj) for obj in bob_docs]}")
