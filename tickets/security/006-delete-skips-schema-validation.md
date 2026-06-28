# Security Finding: Client delete can silently no-op invalid revocations
## Severity
Medium
## Status
Open
## Summary
`ZanzibarClient.write` rejects writes to permissions and unknown relations, but `ZanzibarClient.delete` does not perform the same schema boundary check. A caller can attempt to revoke a computed permission or typo an unknown relation, receive a successful `WriteResult`, and still leave the underlying grant active.

This is an unsafe API semantic for revoke flows: a failed revocation can look successful.
## Affected Area
- `src/zanzipy/client.py` — `write`, `write_many`, `delete`, `_validate_tuple_against_schema`
- Public API: `ZanzibarClient.delete`
- Integration APIs that delegate to `delete`, including `ZanzibarEngine.delete_tuple` and `AuthorizableResource.revoke`
## Critical Flow
1. An application grants a relation such as `document:doc1#owner@user:alice`.
2. A permission such as `can_view` is computed from `owner`.
3. The application attempts to revoke access by calling `client.delete("document:doc1", "can_view", "user:alice")`.
4. `delete` parses the tuple and writes a delete mutation directly.
5. The repository has no stored tuple with relation `can_view`, so the mutation is a no-op.
6. The client returns a `WriteResult` and the permission remains allowed through the underlying `owner` tuple.
## Evidence
Write paths validate schema:

- `src/zanzipy/client.py:116-134` calls `_validate_tuple_against_schema` from `write` and `write_many`.
- `src/zanzipy/client.py:426-430` rejects writes to permissions.

Delete skips that validation:

- `src/zanzipy/client.py:136-143` parses the tuple and writes `TupleMutation.delete(rt)` directly.

A narrow probe reproduced the issue:

1. Schema: `document#can_view = owner`.
2. Wrote `document:doc1#owner@user:alice`.
3. `client.check("document:doc1", "can_view", "user:alice")` returned `True`.
4. Called `client.delete("document:doc1", "can_view", "user:alice")`.
5. Delete returned a `WriteResult` at the current revision.
6. `client.check("document:doc1", "can_view", "user:alice")` still returned `True`.

Existing client tests assert `write` rejects permission and unknown relation writes, and they cover successful delete of a valid relation tuple. No matching test was found for delete of permissions or unknown relations.
## Why This Matters
Revocation paths are security-sensitive. Applications commonly expose “remove access” operations through a high-level API. If a caller passes a permission name instead of the underlying relation name, the library currently reports success while leaving access active.

This is not an authorization-engine allow bypass by itself; it is an unsafe public API semantic that can cause consumers to believe access was revoked when it was not.
## Reproduction or Failure Mode
Minimal scenario:

```python
client.write("document:doc1", "owner", "user:alice")
assert client.check("document:doc1", "can_view", "user:alice") is True
client.delete("document:doc1", "can_view", "user:alice")
assert client.check("document:doc1", "can_view", "user:alice") is True
```

The delete call succeeds even though `can_view` is a permission, not a writable relation tuple.
## Expected Behavior
A client-level delete should reject permission names and unknown relations by default, just as write does.

Delete semantics should clearly distinguish:

- A valid relation tuple that is absent and therefore idempotently deleted.
- A relation/permission name that cannot ever correspond to a stored relation tuple.
- A malformed tuple.
## Suggested Fix
Add schema boundary validation to `ZanzibarClient.delete`, but avoid making stale-tuple cleanup impossible.

Recommended direction:

- Resolve `object namespace + relation` through the current schema.
- Reject unknown relations/permissions and permission names.
- If the relation exists, permit deletion of the specified tuple even if its subject would no longer be accepted for new writes, so callers can clean up stale tuples after schema migrations.

If stricter subject validation is desired, expose an explicit cleanup/delete-unchecked path for removing now-invalid stored tuples.
## Suggested Tests
- `client.delete("document:doc1", "can_view", "user:alice")` raises when `can_view` is a permission.
- `client.delete("document:doc1", "missing", "user:alice")` raises for an unknown relation.
- Valid relation tuple delete remains idempotent when the tuple is absent.
- If schema-narrowed stale tuples are supported for cleanup, add a test proving they can still be deleted intentionally.
## Notes
The repository-level delete primitive can remain schema-agnostic. The issue is the high-level client API returning apparent success for names that the client already knows are not relation tuples.
