import pytest

from zanzipy.models import (
    EntityId,
    IdentifierValidationError,
    NamespaceId,
    Obj,
    Relation,
    RelationTuple,
    Subject,
    TupleFilter,
)


class TestTupleFilter:
    def test_empty_filter_matches_all(self) -> None:
        tuples = [
            RelationTuple.from_string("document:readme#owner@user:alice"),
            RelationTuple.from_string("document:readme#viewer@user:bob"),
            RelationTuple.from_string("folder:docs#viewer@group:eng#member"),
        ]
        tf = TupleFilter()
        assert all(tf.matches(t) for t in tuples)

    def test_filter_by_object_type(self) -> None:
        t1 = RelationTuple.from_string("document:readme#owner@user:alice")
        t2 = RelationTuple.from_string("folder:docs#viewer@user:bob")
        tf = TupleFilter(object_type="document")
        assert tf.matches(t1) is True
        assert tf.matches(t2) is False

    def test_filter_by_object_id(self) -> None:
        t1 = RelationTuple.from_string("document:readme#owner@user:alice")
        t2 = RelationTuple.from_string("document:guide#owner@user:alice")
        tf = TupleFilter(object_id="readme")
        assert tf.matches(t1) is True
        assert tf.matches(t2) is False

    def test_filter_by_relation(self) -> None:
        t1 = RelationTuple.from_string("document:readme#owner@user:alice")
        t2 = RelationTuple.from_string("document:readme#viewer@user:alice")
        tf = TupleFilter(relation="owner")
        assert tf.matches(t1) is True
        assert tf.matches(t2) is False

    def test_filter_by_subject_type_and_id(self) -> None:
        t1 = RelationTuple.from_string("document:readme#owner@user:alice")
        t2 = RelationTuple.from_string("document:readme#owner@user:bob")
        tf = TupleFilter(subject_type="user", subject_id="alice")
        assert tf.matches(t1) is True
        assert tf.matches(t2) is False

    def test_filter_by_subject_relation(self) -> None:
        t1 = RelationTuple.from_string("folder:docs#viewer@group:eng#member")
        t2 = RelationTuple.from_string("folder:docs#viewer@group:eng#admin")
        t3 = RelationTuple.from_string("folder:docs#viewer@user:carol")
        tf = TupleFilter(subject_relation="member")
        assert tf.matches(t1) is True
        assert tf.matches(t2) is False
        assert tf.matches(t3) is False

    def test_direct_subject_relation_filter_matches_only_direct_subject(self) -> None:
        direct = RelationTuple.from_string("folder:docs#viewer@group:eng")
        userset = RelationTuple.from_string("folder:docs#viewer@group:eng#member")
        tf = TupleFilter(subject_relation=TupleFilter.DIRECT_SUBJECT_RELATION)
        assert tf.matches(direct) is True
        assert tf.matches(userset) is False

    def test_combined_filter(self) -> None:
        t1 = RelationTuple.from_string("document:readme#owner@user:alice")
        t2 = RelationTuple.from_string("document:readme#viewer@user:alice")
        tf = TupleFilter(object_type="document", relation="owner")
        assert tf.matches(t1) is True
        assert tf.matches(t2) is False

    def test_from_object_constructor(self) -> None:
        obj = Obj(NamespaceId("document"), EntityId("readme"))
        tf = TupleFilter.from_object(obj)
        t1 = RelationTuple.from_string("document:readme#owner@user:alice")
        t2 = RelationTuple.from_string("document:guide#owner@user:alice")
        assert tf.matches(t1) is True
        assert tf.matches(t2) is False

    def test_from_relation_constructor(self) -> None:
        tf = TupleFilter.from_relation(Relation("owner"))
        t1 = RelationTuple.from_string("document:readme#owner@user:alice")
        t2 = RelationTuple.from_string("document:readme#viewer@user:alice")
        assert tf.matches(t1) is True
        assert tf.matches(t2) is False

    def test_from_subject_constructor(self) -> None:
        subj = Subject(NamespaceId("group"), EntityId("eng"), Relation("member"))
        tf = TupleFilter.from_subject(subj)
        t1 = RelationTuple.from_string("folder:docs#viewer@group:eng#member")
        t2 = RelationTuple.from_string("folder:docs#viewer@group:eng#admin")
        assert tf.matches(t1) is True
        assert tf.matches(t2) is False

    def test_from_subject_constructor_matches_direct_subject_exactly(self) -> None:
        subj = Subject(NamespaceId("group"), EntityId("eng"))
        tf = TupleFilter.from_subject(subj)
        direct = RelationTuple.from_string("folder:docs#viewer@group:eng")
        userset = RelationTuple.from_string("folder:docs#viewer@group:eng#member")
        assert tf.matches(direct) is True
        assert tf.matches(userset) is False

    def test_from_subject_bucket_constructor_matches_subject_variants(self) -> None:
        subj = Subject(NamespaceId("group"), EntityId("eng"))
        tf = TupleFilter.from_subject_bucket(subj)
        direct = RelationTuple.from_string("folder:docs#viewer@group:eng")
        userset = RelationTuple.from_string("folder:docs#viewer@group:eng#member")
        assert tf.matches(direct) is True
        assert tf.matches(userset) is True

    def test_from_parts_constructor(self) -> None:
        obj = Obj(NamespaceId("document"), EntityId("readme"))
        subj = Subject(NamespaceId("user"), EntityId("alice"))
        tf = TupleFilter.from_parts(obj=obj, relation=Relation("owner"), subject=subj)
        t1 = RelationTuple.from_string("document:readme#owner@user:alice")
        t2 = RelationTuple.from_string("document:readme#owner@user:bob")
        t3 = RelationTuple.from_string("document:readme#viewer@user:alice")
        assert tf.matches(t1) is True
        assert tf.matches(t2) is False
        assert tf.matches(t3) is False

    def test_invalid_filter_fields_are_rejected(self) -> None:
        with pytest.raises(IdentifierValidationError):
            TupleFilter(object_type="bad namespace")
