# Security Finding: Durable watch streams can replay same-revision mutations in the wrong order
## Severity
Medium
## Status
Open
## Summary
SQLite and SQLAlchemy repositories reconstruct `watch()` output by selecting created rows and deleted rows separately, then sorting only by revision. Multiple mutations in one `write()` share the same revision, so same-revision delete+write batches for the same tuple can be emitted in an order that does not match the authoritative final snapshot.

A consumer replaying the watch stream can materialize an authorization state that diverges from repository reads.
## Affected Area
- `src/zanzipy/storage/repos/concrete/sqlite/relations.py` — `watch`
- `src/zanzipy/storage/repos/concrete/sqlalchemy/relations.py` — `watch`
- `src/zanzipy/storage/repos/concrete/memory/relations.py` — reference behavior preserving mutation order
- Public API: `RelationRepository.watch`
## Critical Flow
1. A tuple is active at revision 1.
2. A single `write()` call at revision 2 contains conflicting operations for the same tuple, for example delete then touch.
3. The authoritative final snapshot at revision 2 has the tuple active.
4. SQLite/SQLAlchemy `watch(after=revision1)` emits created rows before deleted rows for the same revision.
5. A replay consumer applies write then delete and ends with the tuple inactive.

This makes the change stream disagree with snapshot reads.
## Evidence
SQLite `watch`:

- Selects created rows from `created_revision > after`.
- Selects deleted rows from `deleted_revision > after`.
- Builds all write changes first, then all delete changes.
- Sorts only by `change_revision`.
- Relevant code: `src/zanzipy/storage/repos/concrete/sqlite/relations.py:235-289`.

SQLAlchemy `watch` uses the same split and revision-only sort:

- `src/zanzipy/storage/repos/concrete/sqlalchemy/relations.py:298-364`.

The in-memory backend commits `RelationshipChange` objects in mutation order:

- `src/zanzipy/storage/repos/concrete/memory/relations.py:165-186`.

A storage audit narrow probe observed:

- Seed tuple at revision 1.
- Call `write((TupleMutation.delete(t), TupleMutation.touch(t)))` at revision 2.
- `repo.get(t, revision2)` is active for memory, SQLite, and SQLAlchemy.
- `watch(after=revision1)` emitted:
  - memory: `[('delete', 2), ('write', 2)]`
  - SQLite: `[('write', 2), ('delete', 2)]`
  - SQLAlchemy: `[('write', 2), ('delete', 2)]`

Existing repository watch tests cover separate write/delete revisions, but no test was found for opposing same-revision mutations.
## Why This Matters
`watch()` is the natural API for replication, cache materialization, audit trails, or downstream authorization indexes. If consumers treat it as an authoritative change stream, they can materialize stale or incorrect access state.

Depending on batch order, replay can produce false denies or false allows relative to the repository snapshot. Either breaks authorization consistency.
## Reproduction or Failure Mode
Minimal scenario:

1. Write tuple `t` at revision 1.
2. In one batch, write `(delete(t), touch(t))` at revision 2.
3. Read snapshot revision 2: tuple is active.
4. Replay durable `watch(after=revision1)` into a set.
5. Replayed state ends inactive because the stream emits write before delete.
## Expected Behavior
For any backend, replaying `watch(tenant, after=R)` from a known snapshot at `R` should produce the same state as reading the repository at the resulting head revision.

Within one revision, change ordering must be deterministic and semantically correct, or conflicting same-tuple mutations in one batch must be rejected before commit.
## Suggested Fix
Persist an ordered mutation log with a per-revision sequence number and have `watch()` read that log instead of reconstructing events from tuple visibility windows.

Alternative: normalize or reject conflicting duplicate tuple mutations within a single batch. If a batch cannot contain both delete and touch for the same tuple, the current storage representation is less ambiguous.
## Suggested Tests
- For SQLite and SQLAlchemy: seed an active tuple, call `write((delete(t), touch(t)))`, assert snapshot head has the tuple, replay `watch()` from the previous revision, and assert replayed state equals the snapshot.
- Add the inverse `write((touch(t), delete(t)))` case.
- Add different-tuples-in-same-revision tests to document stable ordering guarantees.
- Add a repository contract test if the intended behavior is to reject conflicting duplicate tuple keys in a batch.
## Notes
Severity depends on whether callers use `watch()` for security-sensitive replication or cache materialization. The API name and revisioned storage model make that use plausible.
