"""
Document Drive: SQLAlchemy-backed authorization storage
------------------------------------------------------

This version keeps the same permission model as ``document_drive.py`` and moves
relation tuples into SQLAlchemy-managed tables. The app still owns its normal
domain tables; zanzipy stores only authorization facts such as
``document:spec#parent@folder:proj``.

Use this when your service already has SQLAlchemy infrastructure and you want
durable, tenant-scoped authorization storage without changing the client API.
This file uses explicit schema objects for full control; most app code can use
the shorter DSL shown in ``document_drive_sqlalchemy_and_dsl.py``.

Requires SQLAlchemy outside a checkout:
    pip install "zanzipy[sqlalchemy]"

Run:
    uv run python examples/document_drive_sqlalchemy.py
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


# Business data: team membership lives in the app database first.
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

# Domain and authorization tables can live beside each other. zanzipy stores
# relation tuples separately from business tables so checks stay auditable.
Base.metadata.create_all(bind=engine)

rel_repo = SQLAlchemyRelationRepository(SessionLocal)
rel_repo.create_schema(engine)

# Optional hot-path cache. Cache entries are tenant/revision-aware, so writes
# move future reads to new keys instead of mutating old snapshots.
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

    # Mirror business membership into authorization tuples. zanzipy does not
    # inspect ORM joins; explicit tuples keep authorization auditable.
    client.write(f"group:{eng.id}", "member", f"user:{bob.id}")
    client.write(f"group:{eng.id}", "member", f"user:{charlie.id}")

    # Create folder and share with team viewers
    client.write("folder:proj", "owner", f"user:{alice.id}")
    client.write("folder:proj", "viewer", f"group:{eng.id}#member")

    # Create a document under the folder, grant Dora edit rights, and ban
    # Charlie to show that exclusions beat inherited group access.
    client.write("document:spec", "owner", f"user:{alice.id}")
    client.write("document:spec", "parent", "folder:proj")
    client.write("document:spec", "editor", f"user:{dora.id}")
    client.write("document:spec", "banned", f"user:{charlie.id}")


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
    print(
        "- Charlie (eng member, but document-banned): "
        f"{chk('document:spec', 'can_view', us['Charlie'])}"
    )
    print(f"- Dora: {chk('document:spec', 'can_view', us['Dora'])}")
    print(f"- Eve: {chk('document:spec', 'can_view', us['Eve'])}")

# Demonstrate cache behavior with repeated queries; subsequent calls should
# hit the LRU cache on object reads and subject-bucket reverse lookup.
with Session(engine) as db:
    us = {u.name: u for u in db.query(User).all()}
    _ = chk("document:spec", "can_view", us["Bob"])  # warm object buckets
    _ = chk("document:spec", "can_view", us["Bob"])  # object-cache hit
    _ = client.expand("document:spec", "can_view")  # object-cache hit
    bob_docs = client.list_objects(
        "document", "can_view", f"user:{us['Bob'].id}"
    )  # warm subject buckets
    bob_docs = client.list_objects(
        "document", "can_view", f"user:{us['Bob'].id}"
    )  # subject-cache hits
print(f"Documents Bob can view via lookup: {bob_docs}")

# Demonstrate revision-scoped cache keys: write/delete create new revisions,
# so old cached buckets remain valid and newer reads use distinct entries.
with Session(engine) as db:
    us = {u.name: u for u in db.query(User).all()}
    before = tuple_cache.info().copy()

    # Write a new relation; subsequent reads use a new revision cache key
    client.write("document:spec", "viewer", f"user:{us['Eve'].id}")
    _ = chk("document:spec", "can_view", us["Bob"])  # triggers re-fill
    after_write = tuple_cache.info().copy()

    # Delete that relation; subsequent reads use another revision cache key
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

print("\nAfter revision-scoped cache demo:")
print("before:", before)
print("after_write:", after_write)
print("after_delete:", after_delete)
