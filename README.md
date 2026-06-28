# zanzipy

**Embedded Zanzibar-style authorization for Python applications.**

zanzipy lets you model relationship-based access control (ReBAC) in your app,
write relationship tuples as facts, and ask authorization questions without
running a separate permissions service. Start in memory, move to SQLAlchemy when
you need durable storage, and keep the same schema and client API.

```text
users -> groups -> folders -> documents
                    \-> inherited permissions, exclusions, reverse lookup
```

## Why teams reach for it

| Need | zanzipy gives you |
| --- | --- |
| Product-friendly permissions | A compact DSL for users, groups, roles, folders, parents, bans, and custom relations. |
| A small Python surface area | `write`, `delete`, `check`, `list_objects`, and `expand` on one client. |
| Real Zanzibar primitives | Relations, computed permissions, unions, intersections, exclusions, subject sets, and tuple-to-userset traversal. |
| Production-shaped storage | Tenant-scoped, revisioned repositories with exact snapshot reads and optional tuple caching. |
| Framework escape hatches | Plain Python, SQLAlchemy repositories, Flask integration, and mixins for domain models. |

## Install

Requires Python 3.14+.

```bash
pip install zanzipy

# Optional integrations used by the examples:
pip install "zanzipy[sqlalchemy]"
pip install "zanzipy[flask,sqlalchemy]"
```

## Quickstart

This is the smallest useful shape: Bob can view a document because he belongs to
a group, the group can view a folder, and the document inherits viewers from its
parent folder.

```python
from zanzipy.client import ZanzibarClient
from zanzipy.dsl import SchemaBuilder
from zanzipy.schema import SchemaRegistry
from zanzipy.storage.repos import InMemoryRelationRepository

schema = (
    SchemaBuilder(SchemaRegistry())
    .namespace("user")
    .done()
    .namespace("group")
    .relation("member", subjects="user")
    .done()
    .namespace("folder")
    .relation("viewer", subjects=["user", "group#member"])
    .permission("can_view", union="viewer")
    .done()
    .namespace("document")
    .relation("parent", subjects="folder")
    .permission("can_view", union="parent->viewer")
    .done()
    .build()
)

authz = ZanzibarClient(
    schema=schema,
    relations_repository=InMemoryRelationRepository(),
)

# Facts you write: group membership, folder sharing, document containment.
authz.write("group:eng", "member", "user:bob")
authz.write("folder:roadmap", "viewer", "group:eng#member")
authz.write("document:q3-plan", "parent", "folder:roadmap")

# Question you ask: does the graph authorize Bob?
assert authz.check("document:q3-plan", "can_view", "user:bob") is True

# Reverse lookup starts from the subject; your app does not scan documents.
assert authz.list_objects("document", "can_view", "user:bob") == ["document:q3-plan"]
```

The important line is `parent->viewer`: zanzipy follows the document's `parent`
edge to the folder and then evaluates the folder's `viewer` relation. That is the
same primitive behind folder inheritance, team sharing, nested groups, and many
other product permission models.

## The model in one minute

- A **tuple** is a fact: `document:q3-plan#parent@folder:roadmap`.
- A **relation** is stored directly: `group:eng#member@user:bob`.
- A **permission** is computed from relations: `can_view = owner + viewer`.
- A **subject set** lets a relation point at another relation: `group:eng#member`.
- A **tuple-to-userset** follows an edge before evaluating a relation:
  `document.parent -> folder.viewer`.
- A **revision token** pins reads to an exact tenant snapshot after a write.

## Client API

```python
write = authz.write("document:q3-plan", "viewer", "user:alice")

authz.check("document:q3-plan", "can_view", "user:alice")
authz.check_at_revision(
    "document:q3-plan",
    "can_view",
    "user:alice",
    revision=write.token,
)

authz.list_objects("document", "can_view", "user:alice")
authz.expand("document:q3-plan", "can_view")
authz.delete("document:q3-plan", "viewer", "user:alice")
```

Use `write_many` when seeding or applying a batch. Use `check_detailed` when you
want the full response and optional debug trace.

## Production shape

zanzipy is intentionally boring where production systems need boring:

- **Tenant isolation:** each `ZanzibarClient` is scoped to a `TenantId`; revision
  tokens carry their tenant and are rejected by clients for other tenants.
- **Revisioned reads:** writes return `WriteResult.token`; exact-revision APIs
  let you evaluate against a known snapshot.
- **Fail-closed traversal:** check, expand, and lookup share a `max_depth` guard;
  recursive graphs are cycle-aware.
- **Schema validation:** tuple writes are checked against relation definitions,
  including allowed subject types.
- **Storage options:** use `InMemoryRelationRepository` for tests and local demos,
  or `SQLAlchemyRelationRepository` for SQLite/PostgreSQL-backed tables managed
  by your app.
- **Hot-path caching:** optional tuple caches are tenant/revision-aware, so stale
  reads do not bleed across snapshots.

## Examples

The examples all use the same document-drive product model and add one integration
layer at a time:

| Example | Shows |
| --- | --- |
| `examples/document_drive.py` | Plain Python, in-memory storage, explicit schema objects, groups, folder inheritance, bans, lookup, and expansion. |
| `examples/document_drive_sqlalchemy.py` | Durable SQLAlchemy relation storage beside normal domain tables. |
| `examples/document_drive_sqlalchemy_and_dsl.py` | The same SQLAlchemy setup using the fluent DSL. |
| `examples/document_drive_sqlalchemy_and_mixins.py` | Domain-friendly `grant`, `check`, `who_can`, and `get_accessible` helpers through mixins. |
| `examples/document_drive_flask_sqlalchemy.py` | Flask extension setup, request-scoped client access, SQLAlchemy storage, mixins, and lookup routes. |

Run the non-server examples from a checkout:

```bash
uv run python examples/document_drive.py
uv run python examples/document_drive_sqlalchemy.py
uv run python examples/document_drive_sqlalchemy_and_dsl.py
uv run python examples/document_drive_sqlalchemy_and_mixins.py
```

Run the Flask example, copy the printed environment variables, then try the
printed `curl` routes:

```bash
uv run python examples/document_drive_flask_sqlalchemy.py
```

## When zanzipy fits

Use it when your app has resources that relate to other resources: documents in
folders, workspaces with teams, projects with roles, organizations with nested
groups, or anything where access is more than a user ID column.

If you already know you need a dedicated multi-language authorization service,
zanzipy is not trying to replace that. It is the Python-native path when you want
the Zanzibar model embedded directly in your application boundary.

## License

Apache-2.0
