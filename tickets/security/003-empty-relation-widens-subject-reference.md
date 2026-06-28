# Security Finding: Empty subject-set relation deserializes as a direct subject reference
## Severity
Medium
## Status
Open
## Summary
`SubjectReference.from_dict` silently converts an empty `relation` value into `None`. A malformed serialized schema that appears to specify a subject-set reference with an empty relation is accepted as a direct namespace subject reference instead.

That changes the schema's allowed-subject contract and can broaden what tuples the client accepts.
## Affected Area
- `src/zanzipy/schema/subjects.py` — `SubjectReference.from_dict`, `SubjectReference.allows`
- `src/zanzipy/schema/relations.py` — relation deserialization using `SubjectReference.from_dict`
- `src/zanzipy/schema/namespace.py` — namespace schema loading
- `src/zanzipy/client.py` — `_validate_tuple_against_schema`
## Critical Flow
1. A schema is loaded from dictionaries, JSON, or another serialized representation.
2. An allowed subject reference contains `{"namespace": "group", "relation": "", "wildcard": false}`.
3. `SubjectReference.from_dict` reads `relation = data.get("relation")` and treats the empty string as falsy.
4. The subject reference becomes `group` direct, not `group#<relation>`.
5. Client write validation accepts direct `group:eng` subjects where the serialized schema likely intended a subject-set form.
## Evidence
`SubjectReference.from_dict` currently uses falsiness to decide whether to construct a relation:

- `src/zanzipy/schema/subjects.py:103-110`

The direct constructor path rejects the same empty relation because it routes through `Relation("")`:

- `src/zanzipy/schema/subjects.py:34-37`
- `src/zanzipy/models/identifier.py:22-29`

A narrow probe showed the widening behavior:

```python
SubjectReference.from_dict({"namespace": "group", "relation": "", "wildcard": False}).to_dict()
# returned {"namespace": "group", "relation": None, "wildcard": False}
```

The direct constructor rejected the same relation:

```python
SubjectReference(namespace="group", relation="")
# raised IdentifierValidationError: identifier cannot be empty
```

Existing tests cover concrete `Subject.from_dict` rejection for empty relations, but no schema `SubjectReference.from_dict` test was found for an empty relation string.
## Why This Matters
Subject-set references and direct subject references have different authorization semantics.

For example, allowing `group#member` means a tuple should point at a group userset such as `group:eng#member`. Allowing direct `group` means a tuple can point at `group:eng` as a principal. Silently converting one to the other can broaden accepted tuples and produce unexpected authorization decisions.

This is especially risky for applications loading schemas from user-maintained YAML/JSON or migration tooling, where an empty string may be produced accidentally.
## Reproduction or Failure Mode
Minimal scenario:

1. Load a relation schema from a dictionary with `allowed_subjects=[{"namespace": "group", "relation": "", "wildcard": false}]`.
2. Build a `ZanzibarClient` from that schema.
3. Write `document:d1#viewer@group:eng`.

The write is accepted as a direct group subject because the empty relation became `None`.
## Expected Behavior
Missing/`None` relation should mean a direct subject reference. Present-but-empty or otherwise invalid relation values should be rejected.

The constructor and deserializer should enforce the same validation semantics.
## Suggested Fix
Change `SubjectReference.from_dict` to distinguish absent/`None` from other falsy values:

- If the `relation` key is absent or its value is `None`, use `relation=None`.
- Otherwise pass the value to `Relation(...)` and let normal validation reject `""`, `False`, `0`, or other invalid values.

Also validate that `wildcard` is a real bool and that wildcard+relation remains rejected after deserialization.
## Suggested Tests
- Add `SubjectReference.from_dict` tests for `relation=""`, `relation=False`, and `relation=0`; assert they raise validation errors.
- Add a namespace/schema load test proving an empty relation in `allowed_subjects` does not become a direct subject reference.
- Add a client write validation test proving a malformed loaded schema cannot accidentally accept direct `group:eng` when the intended shape was subject-set-only.
## Notes
This is a schema boundary issue. It does not require malicious runtime tuple input; a malformed schema file or migration output can change the authorization model before any tuple is written.
