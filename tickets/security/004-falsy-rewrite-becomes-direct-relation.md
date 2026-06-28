# Security Finding: Falsy rewrite payloads deserialize as direct relations
## Severity
Medium
## Status
Open
## Summary
`RelationDef.from_dict` treats any falsy `rewrite` payload as `None`. Because compiled relations with `rewrite is None` become `DirectRule`, malformed serialized rewrite payloads such as `{}`, `[]`, `false`, or `""` can silently become direct relations instead of being rejected.
## Affected Area
- `src/zanzipy/schema/relations.py` — `RelationDef.from_dict`
- `src/zanzipy/schema/rules.py` — `RewriteRule.from_dict`
- `src/zanzipy/schema/compiled.py` — relation compilation to `DirectRule`
- Schema-loading callers that construct `NamespaceDef.from_dict` / `SchemaRegistry` from serialized data
## Critical Flow
1. A relation definition is loaded from a dictionary.
2. The relation has an invalid but falsy `rewrite` payload, for example `{}`.
3. `RelationDef.from_dict` uses `RewriteRule.from_dict(data["rewrite"]) if data["rewrite"] else None`.
4. The invalid rewrite is accepted as `None`.
5. `CompiledAuthorizationModel.from_schema` compiles `rewrite is None` as `DirectRule`.
6. Authorization behavior now differs from the serialized schema.
## Evidence
The deserializer uses a truthiness check:

- `src/zanzipy/schema/relations.py:107-115`

Compilation turns `None` rewrites into direct relations:

- `src/zanzipy/schema/compiled.py:83-87`

A narrow probe reproduced the issue:

```python
RelationDef.from_dict({
    "type": "relation",
    "name": "viewer",
    "allowed_subjects": [{"namespace": "user", "relation": None, "wildcard": False}],
    "rewrite": {},
    "description": None,
}).to_dict()
```

Observed result: a valid relation dictionary with `"rewrite": None`.

Expected result: schema validation error, because `{}` is not a valid rewrite rule.

Existing tests cover valid rewrite round-trips and invalid union/intersection children, but no test was found for falsy invalid relation rewrite payloads.
## Why This Matters
This can silently change authorization semantics during schema loading.

A relation that was intended to have an explicit rewrite can become direct-only. If direct tuples exist or are later written, authorization may allow through a path that the serialized schema did not explicitly declare. At minimum, this creates schema/runtime drift at a security-sensitive boundary.
## Reproduction or Failure Mode
Minimal scenario:

1. Load a relation schema from a dict with `rewrite={}`.
2. Register and compile the namespace.
3. Store a direct tuple for that relation.
4. Check that relation.

The relation behaves as a direct relation because the invalid rewrite was converted to `None`.
## Expected Behavior
Only an explicit `None` should mean “no rewrite/direct relation.” Any other present `rewrite` value must be parsed and validated as a `RewriteRule` or rejected.
## Suggested Fix
Replace the truthiness check with an explicit `is None` check:

```python
raw_rewrite = data["rewrite"]
rewrite = None if raw_rewrite is None else RewriteRule.from_dict(raw_rewrite)
```

Consider also validating the top-level schema definition `type` field while deserializing `RelationDef` and `PermissionDef`, so malformed relation/permission dictionaries cannot be accepted by shape alone.
## Suggested Tests
- Add `RelationDef.from_dict` tests for `rewrite={}`, `rewrite=[]`, `rewrite=False`, and `rewrite=""`; assert they raise.
- Add `NamespaceDef.from_dict` coverage proving invalid relation rewrite payloads are rejected when loading full schemas.
- Add a regression test that only `rewrite=None` compiles to `DirectRule`.
## Notes
This is conservative because direct relations are valid when explicitly declared. The bug is that invalid serialized data takes the same path as an explicit direct relation.
