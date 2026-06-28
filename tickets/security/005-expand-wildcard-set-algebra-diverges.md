# Security Finding: Expand treats namespace wildcards as literal subjects in set algebra
## Severity
Medium
## Status
Open
## Summary
`ExpansionEngine` represents `user:*` as the literal string `"user:*"` and then applies normal Python set intersection/difference. `CheckEngine` treats the same tuple as a namespace wildcard that matches any concrete user.

As a result, `expand` can disagree with `check` and `lookup` for intersection and exclusion rewrites involving wildcards. In exclusion, `expand` can return an overbroad `user:*` grant even when a concrete user is explicitly banned.
## Affected Area
- `src/zanzipy/engine/checker.py` — wildcard matching in `_check_direct`
- `src/zanzipy/engine/expander.py` — `SubjectSet`, `_evaluate_intersection_rule`, `_evaluate_exclusion_rule`, `_expand_direct`, `_materialize`
- Public APIs: `ZanzibarClient.expand`, integration helpers that use expand such as `AuthorizableResource.who_can`
## Critical Flow
1. A relation contains a wildcard tuple such as `document:spec#viewer@user:*`.
2. `CheckEngine` correctly treats that tuple as matching concrete users of the `user` namespace.
3. `ExpansionEngine` stores it as the literal subject string `"user:*"`.
4. Intersection and exclusion materialize subject sets and use literal string set operations.
5. The expansion result no longer matches the semantics of `check`.
## Evidence
`CheckEngine` wildcard behavior:

- `src/zanzipy/engine/checker.py:420-424` treats a stored direct tuple with `subject.id == "*"` as matching any concrete subject id in the same namespace.

`ExpansionEngine` literal set behavior:

- `src/zanzipy/engine/expander.py:31-52` defines `SubjectSet` as two sets of strings.
- `src/zanzipy/engine/expander.py:271-280` performs intersection after materialization.
- `src/zanzipy/engine/expander.py:311-323` performs exclusion as `base_subjects - subtract_subjects`.
- `src/zanzipy/engine/expander.py:391-393` adds `user:*` directly to the `users` bucket.

A narrow probe reproduced divergence:

Schema:

- `can_edit = viewer ∩ editor`
- `can_download = viewer - banned`

Tuples:

- `document:spec#viewer@user:*`
- `document:spec#editor@user:alice`
- `document:spec#banned@user:alice`

Observed results:

- `client.check("document:spec", "can_edit", "user:alice")` returned `True`.
- `client.expand("document:spec", "can_edit")` returned `SubjectSet(users=set(), usersets=set())`.
- `client.check("document:spec", "can_download", "user:alice")` returned `False`.
- `client.check("document:spec", "can_download", "user:bob")` returned `True`.
- `client.expand("document:spec", "can_download")` returned `SubjectSet(users={"user:*"}, usersets=set())`.

Existing tests cover wildcard lookup/check behavior, but no wildcard set-algebra coverage was found in `tests/engine_tests/expander_test.py`.
## Why This Matters
`expand` is a public API and integration helpers can use it to materialize who has access. If callers treat `SubjectSet(users={"user:*"})` as an authoritative ACL for `viewer - banned`, they can over-authorize users who should be excluded.

The intersection case also creates false-deny/partial-authoritative results: `user:* ∩ user:alice` should be representable as `user:alice`, but expansion returns empty.
## Reproduction or Failure Mode
Any permission that combines wildcard grants with finite concrete sets through intersection or exclusion can diverge.

Examples:

- `viewer=user:*`, `editor=user:alice`, `can_edit=viewer ∩ editor` expands to empty even though Alice is allowed.
- `viewer=user:*`, `banned=user:alice`, `can_download=viewer - banned` expands to `user:*` even though Alice is explicitly denied.
## Expected Behavior
Expansion results should not be easier to misuse than check results.

Possible correct behaviors:

- Represent wildcard-with-exceptions explicitly instead of as a plain `user:*` string.
- Materialize finite intersections involving wildcards where possible.
- Return an explicit unsupported/indeterminate result for wildcard exclusion if the result cannot be represented safely.
- Document `expand` as non-authoritative for wildcard set algebra and prevent high-level helpers from exposing it as a complete ACL.
## Suggested Fix
Replace literal string set algebra with a semantic subject-set model that understands namespace wildcards and finite inclusions/exclusions.

At minimum:

- Intersection of `namespace:*` with finite subjects in that namespace should yield those finite subjects.
- Difference of `namespace:* - finite subjects` should not be represented as plain `namespace:*` unless exclusions are preserved.
- Expansion should expose a result type that cannot be mistaken for “all users” when exclusions exist.
## Suggested Tests
- Add an expander test for `viewer=user:*`, `editor=user:alice`, `permission=viewer ∩ editor`; expected expansion should include `user:alice` or explicitly reject unsupported wildcard intersection.
- Add an expander test for `viewer=user:*`, `banned=user:alice`, `permission=viewer - banned`; expected expansion must not return plain `user:*` as an authoritative result.
- Add parity tests comparing `expand`-derived finite membership with `check` for wildcard intersection cases.
## Notes
This finding does not claim check semantics are wrong for wildcard grants. The issue is that `expand` exposes a lossy representation and then performs literal set algebra on it.
