# Security Finding: Exclusion fails open when the subtract branch hits max depth or a cycle
## Severity
High
## Status
Open
## Summary
`ExclusionRule` treats traversal cutoff as a normal negative result. When the base side of an exclusion grants access and the subtract side is truncated by `max_depth` or cycle detection, `CheckEngine` returns `allowed=True` instead of failing closed or surfacing an indeterminate/error state.

The same fail-open shape exists in lookup and expansion: a truncated subtract branch becomes an empty set, then the base set is returned as authoritative.
## Affected Area
- `src/zanzipy/engine/checker.py` — `_check_recursive`, `_evaluate_exclusion_rule`
- `src/zanzipy/engine/lookup.py` — `_lookup_recursive`, `_evaluate_exclusion_rule`
- `src/zanzipy/engine/expander.py` — `_expand_recursive`, `_evaluate_exclusion_rule`
- Public APIs: `ZanzibarClient.check`, `ZanzibarClient.list_objects`, `ZanzibarClient.expand`
## Critical Flow
A caller checks a relation or permission defined as `base - subtract`.

1. The base rule evaluates to true.
2. The subtract rule needs one or more recursive steps.
3. The recursive subtract path exceeds `max_depth` or encounters a cycle.
4. The traversal helper returns `False` / an empty set.
5. Exclusion interprets that result as “the subject is not in the subtract set” and grants from the base.

For negative authorization rules such as `viewer - blocked`, depth/cycle uncertainty is therefore converted into access.
## Evidence
`CheckEngine._check_recursive` returns `False` for both active cycles and max-depth cutoffs:

- `src/zanzipy/engine/checker.py:117-122`

`CheckEngine._evaluate_exclusion_rule` grants when `subtract_ok` is false:

- `src/zanzipy/engine/checker.py:375-388`

`LookupEngine` and `ExpansionEngine` mirror the same set behavior:

- `src/zanzipy/engine/lookup.py:285-293` returns `set()` on depth/unknown paths.
- `src/zanzipy/engine/lookup.py:473-493` returns `base - subtract`.
- `src/zanzipy/engine/expander.py:116-118` returns `SubjectSet()` on cycle/depth cutoff.
- `src/zanzipy/engine/expander.py:293-323` materializes and subtracts the truncated set.

A narrow probe reproduced the check failure with a relation rewrite `viewer = this - blocked`, where both direct tuples exist:

- `document:doc1#viewer@user:alice`
- `document:doc1#blocked@user:alice`

With `max_depth=1`, `client.check("document:doc1", "viewer", "user:alice")` returned `True` and the debug trace included `Max depth reached: 2`. With `max_depth=25`, the same data returned `False`.

A second traversal audit reproduced the same pattern for a deeper subtract chain and observed:

- `check_detailed(...).allowed == True` at the low depth cutoff.
- `list_objects(...) == ["document:secret"]` at the low depth cutoff.
- `expand(...) == SubjectSet(users={"user:alice"}, usersets=set())` at the low depth cutoff.
- All three denied/omitted the subject when the depth limit was raised enough to evaluate the subtract side.

Existing tests cover generic depth cutoff behavior, but they do not exercise a cutoff specifically inside an exclusion subtract/deny branch.
## Why This Matters
This is a plausible unauthorized-access bug. Negative authorization rules are commonly used for bans, blocks, revocations, or explicit denies. A user who is actually present in the subtract set can be authorized if the deny path is deeper than `max_depth` or hidden behind a cycle/cutoff.

Failing closed is especially important because the library currently exposes only boolean check results. There is no `unknown` or `indeterminate` result for callers to handle safely.
## Reproduction or Failure Mode
Minimal scenario:

1. Define a relation rewrite equivalent to `viewer = this - blocked`.
2. Store both `document:doc1#viewer@user:alice` and `document:doc1#blocked@user:alice`.
3. Configure `max_depth` low enough that the subtract side is evaluated through `_check_recursive` past the limit.
4. Check `document:doc1#viewer@user:alice`.

Observed result: allowed.

Expected result: denied or explicit indeterminate/error.
## Expected Behavior
A traversal cutoff in a subtract/deny branch must not be interpreted as proof that the subject is absent from the subtract set.

Safer acceptable behaviors:

- Propagate an explicit indeterminate/error result.
- Fail closed for the whole check when the subtract branch cannot be fully evaluated.
- Reject schemas or configured depth limits that cannot evaluate required deny paths safely.
## Suggested Fix
Introduce an internal tri-state result for recursive evaluation, for example `ALLOW`, `DENY`, `UNKNOWN`, instead of collapsing cutoff/cycle/unknown edges to `False`.

Then update exclusion semantics:

- `base=DENY` → deny.
- `base=UNKNOWN` → unknown/fail closed.
- `base=ALLOW` and `subtract=ALLOW` → deny.
- `base=ALLOW` and `subtract=DENY` → allow.
- `base=ALLOW` and `subtract=UNKNOWN` → unknown/fail closed.

Apply the same principle to lookup and expansion. Do not subtract an incomplete or indeterminate subtract set from the base and return the remainder as authoritative.
## Suggested Tests
- Add a checker test where a positive base relation is true and a subtract chain reaches a direct banned tuple only past `max_depth`; assert the result is not allowed.
- Add a checker cycle variant where the subtract branch contains a cycle; assert the result is not allowed or surfaces an explicit indeterminate/error.
- Add lookup parity coverage for the same fixture; `list_objects` must omit the resource when the subtract side cannot be evaluated safely.
- Add expander coverage for the same fixture; `expand` must not include the base subject when the subtract side is truncated.
## Notes
The current behavior is deny-safe for positive-only paths, but exclusion reverses the meaning of `False` on the subtract side. That makes a generic “cutoff means false” shortcut unsafe in this specific boolean context.
