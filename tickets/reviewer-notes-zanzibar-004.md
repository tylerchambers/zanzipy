# Reviewer notes for Zanzibar ticket 004

## What changed

- Added test-only slow lookup oracle helpers in `tests/engine_tests/lookup_test.py`:
  - `_candidate_resource_objects(...)` enumerates candidate resource objects from visible tuples in the requested resource namespace.
  - `_check_oracle_resources(...)` calls `check` or `check_at_revision` for every candidate and returns the authorized object set.
  - `_assert_lookup_matches_check_oracle(...)` compares production `list_objects` or `list_objects_at_revision` to the oracle as sets and also asserts lookup returns no duplicates.
- Added lookup-vs-check parity tests covering the ticket 004 acceptance matrix:
  - direct relations
  - namespace wildcard subjects
  - nested subject sets
  - cyclic subject sets
  - tuple-to-userset
  - nested tuple-to-userset
  - union, intersection, and exclusion rewrites
  - cross-namespace references
  - tenant isolation
  - exact revision reads
  - max-depth cutoff behavior
  - depth-sensitive non-direct userset validation
  - direct `this` leaves nested under boolean rewrites at low `max_check_depth`
  - exact subject matching inside broad subject-bucket reverse reads
- Left production lookup, check, storage, schema, public API, docs, and examples unchanged.

## Why

Ticket 004 asks for semantic parity evidence:

```text
lookup_resources(resource_type, permission, subject)
==
{object | check(object, permission, subject) is true}
```

The existing lookup tests had strong targeted regressions, but most asserted hand-written expected lists directly. These new helpers make the invariant reusable: production lookup still runs the optimized reverse traversal, while tests independently compute expected results by scanning candidate objects and asking `check`.

The candidate scan is intentionally test-only. It is simple and slow by design, which makes it useful as an oracle and unsuitable as a production `LookupResources` implementation.

## Tradeoffs

- The oracle enumerates candidate objects from relation tuples in the target resource namespace. This avoids needing a separate object catalog, but it means objects with no visible tuples are not candidates. That matches the current storage model used by the library tests: authorization can only be proven from stored relation tuples.
- Assertions compare sets instead of list order. Ticket 004 explicitly says not to assert implementation-specific read ordering; existing public API returns sorted lists today, but parity does not depend on that ordering.
- The tests intentionally reuse `ZanzibarClient` rather than constructing lower-level engines. This exercises the public lookup/check revision and tenant behavior together, but it keeps the oracle tied to client-level validation and consistency APIs.
- The oracle helpers live in `lookup_test.py` rather than shared test utilities. They are currently only needed for lookup parity, and keeping them local avoids exporting a broad test abstraction before another suite needs it.
- I did not update examples or user-facing documentation because ticket 004 adds test coverage only. No public behavior, API shape, docstring contract, or example-facing semantics changed.

## Review focus

- `tests/engine_tests/lookup_test.py`
  - Confirm the oracle helpers are independent enough from production lookup: they read candidate tuples and call `check`; they do not call lookup while constructing expected results.
  - Confirm parity cases cover each acceptance bullet from `tickets/zanzibar-004-lookup-parity-oracle-tests.md`.
  - Confirm exact revision and tenant tests pass revision/tenant context through both lookup and check paths.
  - Confirm low-depth and non-direct userset tests preserve the edge cases from ticket 003 regressions instead of masking them with broad expected sets.

## Verification run

```text
uv run pytest tests/engine_tests/lookup_test.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src tests
```

Latest observed results:

- `uv run pytest tests/engine_tests/lookup_test.py`: `27 passed`
- `uv run ruff check src tests`: `OK`
- `uv run ruff format --check src tests`: `94 files already formatted`
- `uv run ty check src tests`: `All checks passed!`
