from zanzipy.models import RelationTuple, TupleFilter
from zanzipy.storage.repos.concrete._rows import (
    SUBJECT_RELATION_NONE,
    StoredRelationTuple,
    filter_values,
    stored_tuple_values,
    unique_stored_tuples,
)


class _RevisionlessMapping(dict[str, object]):
    def __getitem__(self, key: str) -> object:
        if key in {"created_revision", "deleted_revision"}:
            raise IndexError(key)
        return super().__getitem__(key)


class TestStoredRelationTupleRows:
    def test_direct_subject_round_trip_preserves_deleted_revision(self) -> None:
        tuple_ = RelationTuple.from_string("document:doc1#viewer@user:alice")

        row = StoredRelationTuple.from_tuple(
            tuple_,
            tenant_id="tenant-a",
            created_revision=2,
            deleted_revision=5,
        )

        assert row.subject_rel == SUBJECT_RELATION_NONE
        assert row.to_tuple() == tuple_
        assert row.as_values() == {
            "tenant_id": "tenant-a",
            "tuple_key": "document:doc1#viewer@user:alice",
            "object_ns": "document",
            "object_id": "doc1",
            "relation": "viewer",
            "subject_ns": "user",
            "subject_id": "alice",
            "subject_rel": SUBJECT_RELATION_NONE,
            "created_revision": 2,
            "deleted_revision": 5,
        }

    def test_subject_relation_round_trip_keeps_userset_relation(self) -> None:
        tuple_ = RelationTuple.from_string("document:doc1#viewer@group:eng#member")

        row = StoredRelationTuple.from_tuple(tuple_, tenant_id="tenant-a")

        assert row.subject_rel == "member"
        assert row.to_tuple() == tuple_

    def test_from_mapping_normalizes_none_subject_relation_and_string_revisions(
        self,
    ) -> None:
        mapping: dict[str, object] = {
            "tenant_id": "tenant-a",
            "tuple_key": "document:doc1#viewer@user:alice",
            "object_ns": "document",
            "object_id": "doc1",
            "relation": "viewer",
            "subject_ns": "user",
            "subject_id": "alice",
            "subject_rel": None,
            "created_revision": "3",
            "deleted_revision": "8",
        }

        row = StoredRelationTuple.from_mapping(mapping)

        assert row.subject_rel == SUBJECT_RELATION_NONE
        assert row.created_revision == 3
        assert row.deleted_revision == 8
        assert str(row.to_tuple()) == "document:doc1#viewer@user:alice"

    def test_from_mapping_missing_revision_columns_default_to_none(self) -> None:
        mapping: dict[str, object] = _RevisionlessMapping(
            tenant_id="tenant-a",
            tuple_key="document:doc1#viewer@group:eng#member",
            object_ns="document",
            object_id="doc1",
            relation="viewer",
            subject_ns="group",
            subject_id="eng",
            subject_rel="member",
        )

        row = StoredRelationTuple.from_mapping(mapping)

        assert row.created_revision is None
        assert row.deleted_revision is None
        assert str(row.to_tuple()) == "document:doc1#viewer@group:eng#member"

    def test_stored_tuple_values_include_optional_deleted_revision(self) -> None:
        tuple_ = RelationTuple.from_string("document:doc1#viewer@user:alice")

        values = stored_tuple_values(
            tuple_,
            tenant_id="tenant-a",
            created_revision=4,
            deleted_revision=9,
        )

        assert values["tuple_key"] == str(tuple_)
        assert values["created_revision"] == 4
        assert values["deleted_revision"] == 9

    def test_unique_stored_tuples_keeps_first_canonical_tuple(self) -> None:
        first = RelationTuple.from_string("document:doc1#viewer@user:alice")
        duplicate = RelationTuple.from_string("document:doc1#viewer@user:alice")
        second = RelationTuple.from_string("document:doc2#viewer@user:bob")

        rows = unique_stored_tuples((first, duplicate, second), tenant_id="tenant-a")

        assert [row.tuple_key for row in rows] == [str(first), str(second)]
        assert [row.to_tuple() for row in rows] == [first, second]

    def test_filter_values_only_returns_populated_column_comparisons(self) -> None:
        filter_ = TupleFilter(
            object_type="document",
            relation="viewer",
            subject_relation=TupleFilter.DIRECT_SUBJECT_RELATION,
        )

        assert filter_values(filter_) == [
            ("object_ns", "document"),
            ("relation", "viewer"),
            ("subject_rel", TupleFilter.DIRECT_SUBJECT_RELATION),
        ]
