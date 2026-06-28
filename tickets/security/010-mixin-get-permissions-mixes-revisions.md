# Security Finding: Mixin get_permissions can combine results from multiple revisions
## Severity
Medium
## Status
Open
## Summary
`AuthorizableResource.get_permissions` enumerates permissions and calls `self.check(...)` once per permission without pinning all checks to the same revision. Each check resolves the repository head independently. If tuples change between checks, the returned permission set can describe a state that never existed at any single repository snapshot.
## Affected Area
- `src/zanzipy/integration/mixins.py` — `AuthorizableResource.get_permissions`, `check`, `grant`, `revoke`
- `src/zanzipy/engine_integration.py` — `ZanzibarEngine.check`
- `src/zanzipy/client.py` — `_context_for_consistency`
- Public integration APIs used by domain objects
## Critical Flow
1. A domain object calls `resource.get_permissions(subject)`.
2. The method reads the namespace permissions.
3. For each permission, it calls `self.check(subject, name)`.
4. Each check uses default consistency and resolves the current repository head at that moment.
5. A concurrent grant/revoke between checks can make the returned set combine answers from different revisions.

The result can overstate or understate permissions compared to any actual snapshot.
## Evidence
`get_permissions` loops over permission names and calls `self.check` with no consistency token:

- `src/zanzipy/integration/mixins.py:84-90`

`check` delegates to the configured engine without a consistency parameter:

- `src/zanzipy/integration/mixins.py:61-68`

`ZanzibarEngine.check` supports consistency only when supplied by its caller:

- `src/zanzipy/engine_integration.py:59-73`

`ZanzibarClient._context_for_consistency` resolves default reads by calling `head_revision()` for each check:

- `src/zanzipy/client.py:362-370`

A client API audit probe used two permissions and a mutation between per-permission checks. `get_permissions` returned both permissions even though before the mutation only permission A was true and after the mutation only permission B was true. The returned set was never true at one revision.

Existing mixin tests cover stable cases, but no test was found for shared-snapshot behavior or concurrent mutation between permission checks.
## Why This Matters
Applications can use `get_permissions` to decide which operations to expose, batch-authorize, or cache for a user. A mixed-snapshot permission set can claim a user has a combination of permissions that was never valid, especially during revocation/grant churn.

This is not a single-check bypass; it is an unsafe partial authoritative result in a high-level API.
## Reproduction or Failure Mode
Minimal scenario:

1. Schema has permissions `pa` and `pb`.
2. At revision 1, subject has `pa` only.
3. During `get_permissions`, after `pa` is checked, storage mutates to revoke `pa` and grant `pb`.
4. The next check resolves the new head and returns true for `pb`.
5. `get_permissions` returns `{pa, pb}` even though no revision had both.
## Expected Behavior
A bulk permission query should evaluate against one repository snapshot.

At minimum, the API should make snapshot consistency explicit and avoid presenting mixed-revision results as authoritative.
## Suggested Fix
Resolve one revision token at the start of `get_permissions` and use it for every permission check.

Options:

- Add consistency parameters to mixin read helpers and pass them through to `ZanzibarEngine` / `ZanzibarClient`.
- Have `get_permissions` capture a head token once and evaluate every permission with `AtExactRevision(head_token)`.
- Return `WriteResult` from `grant` / `revoke` so callers can use token-based read-after-write flows through the same integration layer.
## Suggested Tests
- Add a test with two permissions where a mutation occurs between per-permission checks; assert `get_permissions` cannot return a set that was never true at one revision.
- Add a token-pinned variant proving all permission checks use the same captured revision.
- Add read-after-revoke coverage for mixin APIs using `AtLeastAsFresh` or exact revision semantics once exposed.
## Notes
Lower-level client APIs already have consistency concepts. The risk is that the mixin convenience layer hides those concepts at the exact point where callers may treat a multi-permission result as authoritative.
