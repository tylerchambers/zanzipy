## zanzipy ✨

Zanzibar‑style authorization for Python, with a tiny DSL to declare your schema and a simple client to write and check permissions. Friendly, lightweight, and practical.

### Install 📦

Requires Python 3.14+.

```bash
pip install zanzipy
# Optional integrations used by the SQLAlchemy/Flask examples:
pip install "zanzipy[sqlalchemy]"
pip install "zanzipy[flask,sqlalchemy]"
```

### Quick start 🚀
```python
from zanzipy.dsl.builder import SchemaBuilder
from zanzipy.client import ZanzibarClient
from zanzipy.storage.repos import InMemoryRelationRepository, TenantId

# Define schema with the fluent DSL
registry = (
    SchemaBuilder()
        .namespace("user").done()
        .namespace("document")
            .relation("owner", subjects=["user"])  # direct
            .permission("can_view", union=["owner"])  # computed
            .done()
        .build()
)

# Use the tenant-scoped, revisioned in-memory repo for a zero-dependency start.
client = ZanzibarClient(
    schema=registry,
    relations_repository=InMemoryRelationRepository(),
    tenant=TenantId("default"),
)

write = client.write("document:readme", "owner", "user:alice")
assert client.check("document:readme", "can_view", "user:alice")
assert client.check_at_revision(
    "document:readme",
    "can_view",
    "user:alice",
    revision=write.token,
)
```

That’s it. All zanzipy relation storage is tenant-scoped and revisioned. `RelationTuple` stays tenant-free: a tuple is the logical authorization fact (`object#relation@subject`), while `TenantId` is part of read/write/evaluation context. The same logical tuple may exist independently in multiple tenants. Public convenience APIs use the client’s configured tenant and repository head revision by default; exact revision APIs accept tenant-scoped `RevisionToken` values and also allow a naked `Revision` interpreted within the client tenant. Add more relations/permissions with the DSL, and swap the repository when you’re ready to plug in durable storage.

### Quick start with mixins 🧩

Zanzipy also provides convenient mixins for Pythonic integration with your domain models.


```python
from dataclasses import dataclass

from zanzipy.client import ZanzibarClient
from zanzipy.dsl.builder import SchemaBuilder
from zanzipy.engine_integration import ZanzibarEngine, configure_authorization
from zanzipy.integration.mixins import AuthorizableResource, AuthorizableSubject
from zanzipy.storage.repos import InMemoryRelationRepository, TenantId

# Define a minimal schema (user + document)
registry = (
    SchemaBuilder()
        .namespace("user").done()
        .namespace("document")
            .relation("owner", subjects=["user"])  # direct relation
            .permission("can_view", union=["owner"])  # computed permission
            .done()
        .build()
)

# Wire up the engine used by the mixins.
client = ZanzibarClient(
    schema=registry,
    relations_repository=InMemoryRelationRepository(),
    tenant=TenantId("default"),
)
configure_authorization(ZanzibarEngine(client))

# Domain models using mixins
@dataclass
class User(AuthorizableSubject):
    id: str

    def get_subject_dict(self) -> dict:
        return {"namespace": "user", "id": self.id}


@dataclass
class Document(AuthorizableResource):
    id: str

    def get_resource_dict(self) -> dict:
        return {"namespace": "document", "id": self.id}


# Use high‑level helpers
alice = User(id="alice")
readme = Document(id="readme")

readme.grant(alice, "owner")  # writes a tuple: document:readme#owner@user:alice
assert readme.check(alice, "can_view")  # True via the computed permission
```

For a fuller mixins setup with groups, SQLAlchemy models, and caching, see `examples/document_drive_sqlalchemy_and_mixins.py`.

### Examples

The repository examples cover the same document-drive authorization model at increasing integration levels:

- `examples/document_drive.py` — zero-dependency in-memory repository and explicit schema objects.
- `examples/document_drive_sqlalchemy.py` — SQLAlchemy-backed relation storage with explicit schema objects (`zanzipy[sqlalchemy]`).
- `examples/document_drive_sqlalchemy_and_dsl.py` — the same SQLAlchemy setup using `NamespaceBuilder`/`SchemaBuilder`.
- `examples/document_drive_sqlalchemy_and_mixins.py` — domain model helpers via `AuthorizableResource`, `AuthorizableSubject`, and `ZanzibarEngine`.
- `examples/document_drive_flask_sqlalchemy.py` — Flask extension, request-scoped engine binding, mixins, and SQLAlchemy (`zanzipy[flask,sqlalchemy]`).

From a checkout, run the non-server examples with `uv run python <path>`. The Flask example starts a local server and prints IDs for curl requests, including a `list_objects` route that exercises reverse LookupResources.

### Key features 🧰
- ✨ DSL‑first schema authoring (`SchemaBuilder`, `NamespaceBuilder`).
- 🔗 Zanzibar semantics: relations, permissions, union/intersection/exclusion, tuple‑to‑userset.
- ✅ Correctness‑first evaluation: cycle detection, max‑depth limits, and subject expansion.
- 🔎 Reverse lookup: `list_objects` walks subject-bucket edges, so nested usersets and caches work without candidate-object scans.
- Engine-level LookupResources exposes typed request/response models with optional debug traces and traversal counters; the high-level `list_objects` client API still returns `list[str]`.
- 🧩 Simple client API: `write`, `delete`, `check`, `list_objects`, `expand`.
- Tenant-scoped revisioned storage: writes return `WriteResult` with a tenant-scoped `RevisionToken`; snapshot reads prefer the token and reject mismatched tenant tokens.
- Storage-agnostic: implement tenant-scoped `RelationRepository`; start with in-memory. SQLite uses `BEGIN IMMEDIATE` to serialize revision allocation; SQLAlchemy retries transient tenant-revision insert/serialization conflicts and surfaces the database error if retries are exhausted.
- ⚡ Optional tenant/revision-aware tuple cache and compiled rule cache for hot paths.

### When should you use zanzipy? 🤔
- You want ReBAC embedded in your Python app without running another service.
- You prefer a human‑readable, declarative schema (via a tiny DSL).
- Your app has shared resources (e.g., docs, folders, teams) and needs roles, groups, or nested access patterns.
- You need cross‑resource edges (tuple‑to‑userset) and clear, testable authorization logic.

### License
Apache‑2.0


