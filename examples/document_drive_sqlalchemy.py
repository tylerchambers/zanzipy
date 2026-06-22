"""
Document Drive (SQLAlchemy edition)
---------------------------------

Same scenario as `document_drive.py`, but using SQLAlchemy-backed repositories and
proper domain models with stable IDs (UUIDs). This demonstrates how to wire
zanzipy into an app that already uses SQLAlchemy for persistence, while using
the same SQLAlchemy engine for the authorization storage tables.
"""

from uuid import uuid4

from sqlalchemy import Column, ForeignKey, String, Table, create_engine, insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from zanzipy.client import ZanzibarClient
from zanzipy.models import Relation
from zanzipy.schema import (
    ComputedUsersetRule,
    ExclusionRule,
    NamespaceDef,
    PermissionDef,
    RelationDef,
    SchemaRegistry,
    SubjectReference,
    TupleToUsersetRule,
    UnionRule,
)
from zanzipy.storage.cache.concrete.lru import LruTupleCache
from zanzipy.storage.repos.concrete.sqlalchemy import (
    SQLAlchemyRelationRepository,
)

# === Domain models (SQLAlchemy) ==============================================


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


# Association table: team membership
team_members = Table(
    "team_members",
    Base.metadata,
    Column("team_id", String, ForeignKey("teams.id"), primary_key=True),
    Column("user_id", String, ForeignKey("users.id"), primary_key=True),
)


# === Zanzibar schema (same as document drive, with bans and nesting) ===========

user_ns = NamespaceDef(name="user")

group_ns = NamespaceDef(
    name="group",
    relations=(
        RelationDef(
            name="member",
            allowed_subjects=SubjectReference(namespace="user"),
        ),
    ),
)

folder_ns = NamespaceDef(
    name="folder",
    relations=(
        RelationDef(
            name="owner",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        RelationDef(
            name="editor",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        RelationDef(
            name="viewer",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        RelationDef(
            name="banned",
            allowed_subjects=SubjectReference(namespace="user"),
        ),
        RelationDef(
            name="parent",
            allowed_subjects=SubjectReference(namespace="folder"),
        ),
    ),
    permissions=(
        PermissionDef(
            name="can_view",
            rewrite=ExclusionRule(
                base=UnionRule(
                    children=(
                        ComputedUsersetRule("owner"),
                        ComputedUsersetRule("editor"),
                        ComputedUsersetRule("viewer"),
                        TupleToUsersetRule(
                            tuple_relation="parent",
                            computed_relation="viewer",
                        ),
                    )
                ),
                subtract=ComputedUsersetRule("banned"),
            ),
        ),
        PermissionDef(
            name="can_edit",
            rewrite=UnionRule(
                children=(
                    ComputedUsersetRule("owner"),
                    ComputedUsersetRule("editor"),
                )
            ),
        ),
    ),
)

document_ns = NamespaceDef(
    name="document",
    relations=(
        RelationDef(
            name="owner",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        RelationDef(
            name="editor",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        RelationDef(
            name="viewer",
            allowed_subjects=(
                SubjectReference(namespace="user"),
                SubjectReference(namespace="group", relation=Relation("member")),
            ),
        ),
        RelationDef(
            name="banned",
            allowed_subjects=SubjectReference(namespace="user"),
        ),
        RelationDef(
            name="parent",
            allowed_subjects=SubjectReference(namespace="folder"),
        ),
    ),
    permissions=(
        PermissionDef(
            name="can_view",
            rewrite=ExclusionRule(
                base=UnionRule(
                    children=(
                        ComputedUsersetRule("owner"),
                        ComputedUsersetRule("editor"),
                        ComputedUsersetRule("viewer"),
                        TupleToUsersetRule(
                            tuple_relation="parent",
                            computed_relation="viewer",
                        ),
                    )
                ),
                subtract=ComputedUsersetRule("banned"),
            ),
        ),
        PermissionDef(
            name="can_edit",
            rewrite=UnionRule(
                children=(
                    ComputedUsersetRule("owner"),
                    ComputedUsersetRule("editor"),
                )
            ),
        ),
    ),
)

registry = SchemaRegistry()
registry.register_many((user_ns, group_ns, folder_ns, document_ns))


# === Engine, Session, and Repositories =======================================

engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# Create domain tables
Base.metadata.create_all(bind=engine)

# Create authorization tables
rel_repo = SQLAlchemyRelationRepository(SessionLocal)
# The relation repo exposes metadata on the instance; rules metadata is module-level
rel_repo._metadata.create_all(bind=engine)


# Optional: enable an in-memory LRU cache for hot relation tuple reads.
# This wraps the relations repository under the hood and is fully optional.
tuple_cache = LruTupleCache(max_entries=10_000, ttl_seconds=30)

client = ZanzibarClient(
    schema=registry,
    relations_repository=rel_repo,
    tuple_cache=tuple_cache,
)


# === Seed domain and tuples ===================================================


def new_user(name: str) -> User:
    return User(id=str(uuid4()), name=name)


def new_team(name: str) -> Team:
    return Team(id=str(uuid4()), name=name)


with Session(engine) as db:
    alice = new_user("Alice")
    bob = new_user("Bob")
    charlie = new_user("Charlie")
    dora = new_user("Dora")
    eve = new_user("Eve")
    eng = new_team("Engineering")

    db.add_all([alice, bob, charlie, dora, eve, eng])
    db.commit()

    # team membership: add Bob and Charlie
    db.execute(
        insert(team_members).values(
            [
                {"team_id": eng.id, "user_id": bob.id},
                {"team_id": eng.id, "user_id": charlie.id},
            ]
        )
    )
    db.commit()

    # Mirror the domain membership into zanzipy tuples via subject-set
    # group namespace uses 'group', but our table is named teams
    client.write(f"group:{eng.id}", "member", f"user:{bob.id}")
    client.write(f"group:{eng.id}", "member", f"user:{charlie.id}")

    # Create folder and share with team viewers
    client.write("folder:proj", "owner", f"user:{alice.id}")
    client.write("folder:proj", "viewer", f"group:{eng.id}#member")

    # Create document under folder and share editor with Dora
    client.write("document:spec", "owner", f"user:{alice.id}")
    client.write("document:spec", "parent", "folder:proj")
    client.write("document:spec", "editor", f"user:{dora.id}")


# === Demo checks and expansion ===============================================


def chk(obj: str, rel: str, user: User) -> bool:
    return client.check(obj, rel, f"user:{user.id}")


print("=== Document Drive (SQLAlchemy) ===")
print("Folder viewing:")
with Session(engine) as db:
    us = {u.name: u for u in db.query(User).all()}
    print(f"- Alice: {chk('folder:proj', 'can_view', us['Alice'])}")
    print(f"- Bob: {chk('folder:proj', 'can_view', us['Bob'])}")
    print(f"- Eve: {chk('folder:proj', 'can_view', us['Eve'])}")

print("Document viewing:")
with Session(engine) as db:
    us = {u.name: u for u in db.query(User).all()}
    print(f"- Alice: {chk('document:spec', 'can_view', us['Alice'])}")
    print(f"- Bob: {chk('document:spec', 'can_view', us['Bob'])}")
    print(f"- Charlie: {chk('document:spec', 'can_view', us['Charlie'])}")
    print(f"- Dora: {chk('document:spec', 'can_view', us['Dora'])}")
    print(f"- Eve: {chk('document:spec', 'can_view', us['Eve'])}")

# Demonstrate cache behavior with repeated queries; subsequent calls should
# hit the LRU cache on relation tuple reads (by-object and/or reverse reads).
with Session(engine) as db:
    us = {u.name: u for u in db.query(User).all()}
    _ = chk("document:spec", "can_view", us["Bob"])  # warm cache
    _ = chk("document:spec", "can_view", us["Bob"])  # likely cache hit
    _ = client.expand("document:spec", "can_view")  # warm
    _ = client.expand("document:spec", "can_view")  # likely hit

# Demonstrate invalidation: perform a write/delete on the same object/subject
# and re-read to show cache misses increase.
with Session(engine) as db:
    us = {u.name: u for u in db.query(User).all()}
    before = tuple_cache.info().copy()

    # Write a new relation on the same object to invalidate by-object cache
    client.write("document:spec", "viewer", f"user:{us['Eve'].id}")
    _ = chk("document:spec", "can_view", us["Bob"])  # triggers re-fill
    after_write = tuple_cache.info().copy()

    # Delete that relation, invalidating again
    client.delete("document:spec", "viewer", f"user:{us['Eve'].id}")
    _ = chk("document:spec", "can_view", us["Bob"])  # triggers re-fill
    after_delete = tuple_cache.info().copy()

print("Document editing:")
with Session(engine) as db:
    us = {u.name: u for u in db.query(User).all()}
    print(f"- Alice: {chk('document:spec', 'can_edit', us['Alice'])}")
    print(f"- Dora: {chk('document:spec', 'can_edit', us['Dora'])}")
    print(f"- Bob: {chk('document:spec', 'can_edit', us['Bob'])}")

print("\nExpansion (who can view?):")
fold_view = client.expand("folder:proj", "can_view")
print(f"folder:proj users: {sorted(fold_view.users)}")
doc_view = client.expand("document:spec", "can_view")
print(f"document:spec users: {sorted(doc_view.users)}")

# Print cache stats to show hit/miss counts gathered during the demo.
stats = tuple_cache.info()
print("\nCache stats:")
print(
    "hits=",
    stats.get("hits"),
    "misses=",
    stats.get("misses"),
    "size_objects=",
    stats.get("size_objects"),
    "size_subjects=",
    stats.get("size_subjects"),
)

print("\nAfter invalidation demo:")
print("before:", before)
print("after_write:", after_write)
print("after_delete:", after_delete)
