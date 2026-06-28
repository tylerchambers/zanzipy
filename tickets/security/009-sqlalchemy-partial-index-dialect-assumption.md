# Security Finding: SQLAlchemy active-tuple uniqueness assumes partial-index dialect support
## Severity
Medium
## Status
Open
## Summary
The SQLAlchemy repository declares the active-tuple uniqueness index with `sqlite_where` and `postgresql_where`. On dialects that do not support those options, such as MySQL/MariaDB, SQLAlchemy can compile an unconditional unique index on `(tenant_id, tuple_key)`.

That breaks revisioned delete/re-add semantics by preventing historical rows for the same tuple key.
## Affected Area
- `src/zanzipy/storage/repos/concrete/sqlalchemy/relations.py` — table/index definition
- SQLAlchemy deployments outside SQLite/PostgreSQL
- Repository contract around revisions and historical tuple rows
## Critical Flow
1. A deployment uses `SQLAlchemyRelationRepository` on a dialect without filtered/partial unique index support.
2. Schema creation compiles `idx_rt_active_unique` as an unconditional unique index on `(tenant_id, tuple_key)`.
3. A tuple is written and later deleted.
4. Re-adding the same tuple requires inserting a new historical row with the same `(tenant_id, tuple_key)` and a new `created_revision`.
5. The unconditional unique index rejects the insert.

Memory, SQLite, and PostgreSQL semantics allow delete/re-add while preserving historical revisions.
## Evidence
Index definition:

- `src/zanzipy/storage/repos/concrete/sqlalchemy/relations.py:97-104`

The index is declared as unique over `tenant_id` and `tuple_key`, with dialect-specific partial predicates only for SQLite and PostgreSQL.

A storage audit compiled the index for multiple dialects and observed:

- SQLite/PostgreSQL: `CREATE UNIQUE INDEX ... WHERE deleted_revision IS NULL`
- MySQL: `CREATE UNIQUE INDEX idx_rt_active_unique ON relation_tuples (tenant_id, tuple_key)`

Existing SQLAlchemy tests cover SQLite/PostgreSQL index behavior and delete/re-add repository semantics, but no MySQL/MariaDB compile or behavior test was found.
## Why This Matters
Delete/re-add is part of the revisioned authorization model. A revoked relationship may later need to be granted again, while older exact-revision reads still need to see historical state.

On unsupported dialects, the repository can fail to restore access that should be granted, diverging from other backends. That is an authorization correctness issue and a deployment footgun.
## Reproduction or Failure Mode
Minimal scenario on a dialect where the partial predicate is not honored:

1. Create the SQLAlchemy repository schema.
2. Write `document:doc1#viewer@user:alice` at revision 1.
3. Delete it at revision 2.
4. Re-add it at revision 3.

Expected repository semantics: revision 1 visible, revision 2 absent, revision 3 visible.

Failure mode: the revision 3 insert violates the unconditional `(tenant_id, tuple_key)` unique index.
## Expected Behavior
The repository should either:

- Refuse to create/use schemas on dialects that cannot enforce “only one active tuple row” while preserving historical rows, or
- Implement a dialect-specific schema that preserves the same revision semantics.
## Suggested Fix
Add an explicit dialect capability check in `create_schema` or repository initialization.

If only SQLite and PostgreSQL are supported, fail fast with a clear error on other dialects.

If MySQL/MariaDB support is intended, use a dialect-compatible design, such as:

- Generated columns for active tuple keys.
- A separate current-state table plus ordered history table.
- Another uniqueness strategy that permits multiple historical rows but only one active row.
## Suggested Tests
- Add a SQLAlchemy dialect compilation test for MySQL/MariaDB that asserts either a clear unsupported-dialect failure or a correct dialect-specific active-unique strategy.
- Add a delete/re-add integration test for every supported SQLAlchemy dialect.
- Add documentation/tests that define the supported SQLAlchemy dialect set.
## Notes
The current README mentions SQLAlchemy support generally and specifically calls out SQLite/PostgreSQL-like conflict behavior only indirectly. If SQLAlchemy support is intentionally limited by dialect, the runtime should enforce that contract.
