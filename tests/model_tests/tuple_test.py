import pytest

from zanzipy.models import (
    EntityId,
    IdentifierValidationError,
    InvalidTupleFormatError,
    NamespaceId,
    Obj,
    Relation,
    RelationTuple,
    Subject,
)


class TestRelationTuple:
    BASIC = "document:readme#owner@user:alice"
    SUBJECT_REL = "folder:docs#viewer@group:eng#member"

    @pytest.mark.parametrize(
        ("tuple_string", "expected_object", "relation", "subject"),
        [
            (BASIC, "document:readme", "owner", "user:alice"),
            (SUBJECT_REL, "folder:docs", "viewer", "group:eng#member"),
            (
                "document-drive:file123#can-read@auth-service:user456",
                "document-drive:file123",
                "can-read",
                "auth-service:user456",
            ),
        ],
    )
    def test_from_string_round_trip(
        self,
        tuple_string: str,
        expected_object: str,
        relation: str,
        subject: str,
    ) -> None:
        t = RelationTuple.from_string(tuple_string)
        assert str(t.object) == expected_object
        assert str(t.relation) == relation
        assert str(t.subject) == subject
        assert str(t) == tuple_string

    @pytest.mark.parametrize(
        "tuple_string",
        [
            "document:readme#owneruser:alice",  # missing '@'
            "document:readme@user:alice",  # missing '#'
            "document#owner@user:alice",  # invalid object (missing id)
            "document:readme#owner@user",  # invalid subject (missing id)
            "document:readme#@user:alice",  # empty relation
        ],
    )
    def test_invalid_formats(self, tuple_string: str) -> None:
        with pytest.raises(InvalidTupleFormatError):
            RelationTuple.from_string(tuple_string)

    @pytest.mark.parametrize(
        "tuple_string",
        [
            "doc ument:readme#owner@user:alice",
            "document:read me#owner@user:alice",
            "document:readme#own er@user:alice",
            "document:readme#owner@us er:alice",
            "document:readme#owner@user:ali ce",
            "document:readme#owner@user:alice#mem ber",
            " document:readme#owner@user:alice",
            "document:readme#owner@user:alice ",
            "\tdocument:readme#owner@user:alice",
        ],
    )
    def test_whitespace_rejected(self, tuple_string: str) -> None:
        with pytest.raises(InvalidTupleFormatError):
            RelationTuple.from_string(tuple_string)

    def test_empty_subject_relation(self) -> None:
        with pytest.raises(InvalidTupleFormatError):
            RelationTuple.from_string("doc:id#rel@sub:id#")

    @pytest.mark.parametrize(
        "tuple_string",
        [
            "doc:re#adme#owner@user:alice",
            "doc:readme#own@er@user:alice",
            "doc:re:adme#owner@user:alice",
            "doc:readme#own:er@user:alice",  # relation contains colon
        ],
    )
    def test_special_characters_rejected(self, tuple_string: str) -> None:
        with pytest.raises(InvalidTupleFormatError):
            RelationTuple.from_string(tuple_string)

    def test_hashable(self) -> None:
        t1 = RelationTuple.from_string(self.BASIC)
        t2 = RelationTuple.from_string(self.BASIC)
        assert t1 == t2
        assert hash(t1) == hash(t2)
        assert len({t1, t2}) == 1

    def test_unicode(self) -> None:
        t = RelationTuple.from_string("document:文档#owner@user:alice")
        assert str(t.object.id) == "文档"

    def test_direct_instantiation_validates(self) -> None:
        t = RelationTuple(
            Obj(NamespaceId("document"), EntityId("readme")),
            Relation("owner"),
            Subject(NamespaceId("user"), EntityId("alice")),
        )
        assert str(t) == self.BASIC

    def test_repr(self) -> None:
        t = RelationTuple.from_string(self.BASIC)
        assert repr(t) == (
            "RelationTuple(object_namespace='document', object_id='readme', "
            "relation='owner', "
            "subject_namespace='user', subject_id='alice', "
            "subject_relation=None)"
        )

    def test_to_dict_without_subject_relation(self) -> None:
        t = RelationTuple.from_string(self.BASIC)
        assert t.to_dict() == {
            "object": {"namespace": "document", "id": "readme"},
            "relation": "owner",
            "subject": {"namespace": "user", "id": "alice", "relation": None},
        }

    def test_from_dict_without_subject_relation_key(self) -> None:
        # New nested structure (no subject relation key)
        nested = {
            "object": {"namespace": "document", "id": "readme"},
            "relation": "owner",
            "subject": {"namespace": "user", "id": "alice", "relation": None},
        }
        t = RelationTuple.from_dict(nested)
        assert str(t) == self.BASIC

    def test_to_from_dict_round_trip_with_subject_relation(self) -> None:
        original = RelationTuple.from_string(self.SUBJECT_REL)
        as_dict = original.to_dict()
        restored = RelationTuple.from_dict(as_dict)
        assert restored == original
        assert str(restored) == self.SUBJECT_REL

    def test_from_dict_validation_errors(self) -> None:
        # Empty object_namespace
        # Nested invalid subject relation (empty string should raise)
        bad_nested = {
            "object": {"namespace": "document", "id": "readme"},
            "relation": "owner",
            "subject": {"namespace": "user", "id": "alice", "relation": ""},
        }
        with pytest.raises(IdentifierValidationError):
            RelationTuple.from_dict(bad_nested)
