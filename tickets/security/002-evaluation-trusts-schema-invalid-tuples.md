# Security Finding: Evaluation trusts schema-invalid stored tuples
## Severity
High
## Status
Open
## Summary
Tuple writes through `ZanzibarClient.write` are validated against the schema, but authorization evaluation does not enforce `RelationDef.allowed_subjects` when reading stored tuples. If tuples enter storage through a lower-level repository, data import, cache, corruption, or an older schema version, the engines can treat schema-invalid tuples as authoritative grants.

The same root cause means narrowing a schema does not invalidate existing tuples that are no longer allowed by the current schema, even after rebuilding the client/engine.
## Affected Area
- `src/zanzipy/client.py` — `write`, `write_many`, `_validate_tuple_against_schema`
- `src/zanzipy/engine/checker.py` — `_check_direct`
- `src/zanzipy/engine/lookup.py` — reverse traversal over stored tuples
- `src/zanzipy/engine/expander.py` — direct tuple expansion
- `src/zanzipy/schema/compiled.py` — compiled relation metadata does not carry direct allowed-subject enforcement into check traversal
- Public lower-level APIs: `RelationRepository.write`, `AuthorizationEngine.check`, `CheckEngine.check`
## Critical Flow
1. A relation schema declares allowed subjects for a relation.
2. `ZanzibarClient.write` enforces that contract for new writes.
3. The repository stores tuples independently of the schema.
4. `CheckEngine._check_direct` reads tuples for the object/relation and accepts direct matches or recursively follows subject sets without checking whether that stored subject shape is allowed by the relation definition.
5. A schema-invalid tuple can therefore grant access.

This affects direct subjects, subject-set subjects, and wildcard subjects. A stored `user:*` tuple is especially dangerous because `CheckEngine` treats `*` as a namespace wildcard during evaluation.
## Evidence
Write-time validation exists:

- `src/zanzipy/client.py:119-132` parses tuples and calls `_validate_tuple_against_schema` for `write` / `write_many`.
- `src/zanzipy/client.py:420-464` checks `RelationDef.allowed_subjects` before accepting a write.

Read-time evaluation does not repeat that invariant:

- `src/zanzipy/engine/checker.py:412-443` iterates stored tuples for the object, filters by relation name, accepts direct subject matches including `*`, and recursively follows any subject-set tuple.

A narrow probe reproduced a schema-invalid subject-set grant:

- Schema: `document#viewer` allows only direct `user` subjects.
- Schema: `group#member` allows direct `user` subjects.
- Repository was seeded directly with:
  - `group:eng#member@user:alice`
  - `document:doc1#viewer@group:eng#member` — invalid for `document#viewer`.
- `AuthorizationEngine.check(document:doc1#viewer@user:alice)` returned `CheckResponse(allowed=True, depth_reached=1, tuples_examined=2)`.

A second probe reproduced schema narrowing drift:

1. Schema v1 allowed `document#viewer@user`.
2. `client_v1.write("document:d1", "viewer", "user:alice")` succeeded.
3. Schema was updated so `document#viewer` allowed only `group#member`.
4. A rebuilt client using the updated schema rejected a new `user:alice` write with `ValueError: Subject not allowed by schema`.
5. The rebuilt client still returned `True` for `client_v2.check("document:d1", "viewer", "user:alice")` because the old tuple remained authoritative.

Existing tests cover write-time schema rejection, but no test was found that injects a schema-invalid active tuple directly into storage or checks behavior after `allowed_subjects` narrows.
## Why This Matters
This can produce an incorrect allow decision.

Consumers often import relationship data, migrate schemas, use lower-level repositories in tests/tools, or wrap storage backends. If the evaluation path trusts storage rows without enforcing the current schema, any invalid active tuple becomes an authorization fact. A schema migration that removes an allowed subject type also does not revoke old grants of that type.
## Reproduction or Failure Mode
Minimal invalid-storage scenario:

1. Define `document#viewer` as allowing only direct `user` subjects.
2. Bypass `ZanzibarClient.write` and write `document:doc1#viewer@group:eng#member` directly to a repository.
3. Write `group:eng#member@user:alice`.
4. Check `document:doc1#viewer@user:alice`.

Observed result: allowed.

Minimal schema-narrowing scenario:

1. Write a tuple valid under schema v1.
2. Update schema v2 so that tuple subject shape is no longer allowed.
3. Rebuild the client/engine with schema v2.
4. Check the old tuple.

Observed result: still allowed.
## Expected Behavior
The current schema must be the source of truth for authorization semantics.

At least one of these should hold:

- Evaluation ignores or rejects stored tuples that do not conform to the current relation definition.
- Schema updates that would leave incompatible active tuples are rejected until a migration removes or rewrites them.
- The project explicitly documents that stored tuples are trusted, schema changes do not revoke existing grants, and callers must run a provided migration scanner before treating a new schema as authoritative.
## Suggested Fix
Compile allowed-subject metadata into `CompiledAuthorizationModel` and validate tuples at evaluation boundaries before using them.

For example:

- For direct tuple evaluation, confirm the tuple subject is allowed by the relation being evaluated before accepting it or recursing into its subject set.
- For wildcard subjects, only treat `subject.id == "*"` as a wildcard if the relation explicitly allows `SubjectReference(..., wildcard=True)`.
- For tuple-to-userset traversal, ensure the tuple relation's subject is one of the compiled direct object target namespaces.

If runtime validation is considered too expensive, add an explicit schema migration/validation API that scans active tuples and fails schema activation when incompatible tuples remain.
## Suggested Tests
- Inject a schema-invalid subject-set tuple directly into an in-memory repository and assert `check`, `list_objects`, and `expand` do not treat it as authorized.
- Inject a schema-invalid wildcard tuple such as `document:d1#owner@user:*` where `owner` allows only direct users; assert it does not grant all users.
- Write a valid tuple under schema v1, narrow `allowed_subjects` under schema v2, rebuild the engine, and assert the old tuple is rejected or the schema activation fails.
- Repeat the schema-narrowing test for direct subjects, subject-set subjects, and wildcard subjects.
## Notes
This finding does not require treating every repository as hostile. It is enough that repository contents can be stale, imported, migrated, corrupted, or written through public lower-level APIs. Authorization evaluation should not silently convert invalid storage state into grants.
