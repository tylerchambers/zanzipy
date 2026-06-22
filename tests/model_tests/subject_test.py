import pytest

from zanzipy.models import (
    EntityIdValidationError,
    IdentifierValidationError,
    Subject,
    SubjectValidationError,
)


class TestSubject:
    @pytest.mark.parametrize(
        ("subject_string", "expected"),
        [
            ("user:alice", "user:alice"),
            ("group:eng#member", "group:eng#member"),
        ],
    )
    def test_from_string_and_str(self, subject_string: str, expected: str) -> None:
        s = Subject.from_string(subject_string)
        assert str(s) == expected

    @pytest.mark.parametrize(
        ("subject_string", "exc_type"),
        [
            ("user", SubjectValidationError),  # missing ':'
            ("user:", EntityIdValidationError),  # empty id
            (":alice", IdentifierValidationError),  # empty namespace
            ("user:al ice", EntityIdValidationError),  # spaces in id
            ("user:alice#", SubjectValidationError),  # empty relation
        ],
    )
    def test_invalid_subjects(
        self, subject_string: str, exc_type: type[Exception]
    ) -> None:
        with pytest.raises(exc_type):
            Subject.from_string(subject_string)

    def test_string_representation(self) -> None:
        s = Subject.from_string("user:alice#member")
        assert str(s) == "user:alice#member"

    def test_to_from_dict_round_trip_with_relation(self) -> None:
        original = Subject.from_string("group:eng#member")
        as_dict = original.to_dict()
        assert as_dict == {"namespace": "group", "id": "eng", "relation": "member"}
        restored = Subject.from_dict(as_dict)
        assert restored == original

    def test_to_from_dict_round_trip_without_relation(self) -> None:
        original = Subject.from_string("user:alice")
        as_dict = original.to_dict()
        assert as_dict == {"namespace": "user", "id": "alice", "relation": None}
        restored = Subject.from_dict(as_dict)
        assert restored == original

    def test_from_dict_without_relation_key(self) -> None:
        assert Subject.from_dict({"namespace": "user", "id": "alice"}) == (
            Subject.from_string("user:alice")
        )

    def test_from_dict_rejects_empty_relation(self) -> None:
        with pytest.raises(IdentifierValidationError):
            Subject.from_dict({"namespace": "user", "id": "alice", "relation": ""})

    def test_require_direct_rejects_subject_set(self) -> None:
        with pytest.raises(SubjectValidationError, match="direct subject"):
            Subject.from_string("group:eng#member").require_direct()
