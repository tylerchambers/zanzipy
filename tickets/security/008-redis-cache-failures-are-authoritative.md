# Security Finding: Redis tuple cache failures are authoritative in read paths
## Severity
Medium
## Status
Open
## Summary
`CachedRelationRepository` treats tuple-cache operations as part of the authorization read path. Redis cache `get`, `set`, and corrupt-payload eviction errors propagate instead of falling back to the authoritative repository.

A non-authoritative acceleration layer can therefore turn authorization checks, lookup, or expansion into errors before the durable relation repository is consulted.
## Affected Area
- `src/zanzipy/storage/repos/decorators/cached_relations.py` — `_object_bucket`, `_subject_bucket`
- `src/zanzipy/storage/cache/concrete/redis.py` — `get_by_object`, `set_by_object`, `get_by_subject`, `set_by_subject`
- Public configuration: `AuthorizationEngine(..., tuple_cache=...)`, `ZanzibarClient(..., tuple_cache=...)`
## Critical Flow
1. A client is configured with a tuple cache, for example Redis.
2. Authorization evaluation reads an object or subject bucket through `CachedRelationRepository`.
3. The cache `get` raises due to Redis outage, timeout, permission error, or client failure.
4. The exception propagates and the backend repository is not read.

A similar issue happens after a successful backend read if cache `set` fails, and while evicting corrupt cache values if `delete` fails.
## Evidence
`CachedRelationRepository._object_bucket` calls cache first and only falls back on `None`:

- `src/zanzipy/storage/repos/decorators/cached_relations.py:131-142`

`CachedRelationRepository._subject_bucket` has the same behavior:

- `src/zanzipy/storage/repos/decorators/cached_relations.py:144-156`

`RedisTupleCache` directly propagates client errors:

- `src/zanzipy/storage/cache/concrete/redis.py:93-101` calls `get`, decodes, and calls `delete` on decode error.
- `src/zanzipy/storage/cache/concrete/redis.py:111-113` calls `set` after object bucket reads.
- `src/zanzipy/storage/cache/concrete/redis.py:140-142` calls `set` after subject bucket reads.

Existing Redis tests cover happy paths, tenant/revision keying, TTL arguments, and corrupt payloads when delete succeeds. No test was found for Redis `get`, `set`, or `delete` exceptions or cached-repository fallback behavior.
## Why This Matters
Redis is a cache, not the source of truth. Making cache availability authoritative creates denial-of-service risk and unsafe unknown/error semantics in the authorization path.

If application code mishandles authorization exceptions, cache outages can also become fail-open at the application boundary. Even when applications fail closed, a cache outage can deny all protected operations despite the durable repository being healthy.
## Reproduction or Failure Mode
Minimal scenario:

1. Wrap a healthy relation repository in `CachedRelationRepository`.
2. Use a cache implementation whose `get_by_object` raises `ConnectionError`.
3. Call `check` on a tuple that is present in the backend repository.

Observed behavior from source: cache exception propagates before backend read.

Similar scenarios:

- Backend read succeeds, but cache `set_by_object` raises; authorization read fails after retrieving correct data.
- Cache payload is corrupt, decode raises, and cache `delete` raises; cache maintenance failure becomes authorization failure.
## Expected Behavior
Cache failures should not be authoritative for authorization reads.

Recommended behavior:

- Treat cache get failures as cache misses.
- Return backend results even if cache set fails.
- Treat corrupt payloads as misses even if eviction fails.
- Keep `ping()` / health checks as the place where cache availability is surfaced explicitly.
## Suggested Fix
Catch cache exceptions in `CachedRelationRepository`, not only in `RedisTupleCache`, because the decorator owns the policy that cache is non-authoritative.

Suggested policy:

- `_object_bucket`: on `cache.get_by_object` exception, read backend; on `cache.set_by_object` exception, return backend result anyway.
- `_subject_bucket`: same for reverse/subject buckets.
- Optionally log cache failures through a caller-provided hook, but do not change the authorization answer when the backend succeeds.
## Suggested Tests
- Cached repository test: cache `get_by_object` raises, backend has tuple, `read` returns backend tuple.
- Cached repository test: backend read succeeds and cache `set_by_object` raises, `read` still returns backend tuple.
- Subject-bucket equivalents for `read_reverse` / lookup flows.
- Cached repository test: use a cache implementation whose corrupt-payload eviction path raises during bucket retrieval; assert the repository treats the cache failure as a miss and still returns the backend result.
## Notes
This finding is about cache failure semantics, not Redis trust. A poisoned cache with valid but incorrect tuple payloads is a separate deployment trust concern.
