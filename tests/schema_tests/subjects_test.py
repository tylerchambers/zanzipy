import pytest

from zanzipy.models.errors import IdentifierValidationError
from zanzipy.models.namespace import NamespaceId
from zanzipy.models.relation import Relation
from zanzipy.schema.subjects import SubjectReference


class TestSubjectReference:
    def test_valid_namespace_only(self) -> None:
        s = SubjectReference(namespace=NamespaceId("user"))
        assert s.namespace.value == "user"
        assert s.relation is None
        assert s.wildcard is False

    def test_valid_namespace_and_relation(self) -> None:
        s = SubjectReference(
            namespace=NamespaceId("group"), relation=Relation("member")
        )
        assert s.namespace.value == "group"
        assert s.relation is not None
        assert s.relation.value == "member"
        assert s.wildcard is False

    def test_valid_namespace_wildcard(self) -> None:
        s = SubjectReference(namespace=NamespaceId("user"), wildcard=True)
        assert s.namespace.value == "user"
        assert s.relation is None
        assert s.wildcard is True

    def test_wildcard_and_relation_mutually_exclusive(self) -> None:
        with pytest.raises(
            ValueError, match="wildcard and relation are mutually exclusive"
        ):
            SubjectReference(
                namespace=NamespaceId("group"),
                relation=Relation("member"),
                wildcard=True,
            )

    @pytest.mark.parametrize(
        "ns",
        [
            "user",
            "A",
            "_abc",
            "name-1",
        ],
    )
    def test_valid_namespace_identifiers(self, ns: str) -> None:
        s = SubjectReference(namespace=NamespaceId(ns))
        assert s.namespace.value == ns

    @pytest.mark.parametrize(
        "ns",
        [
            "",
            "1starts_with_digit",
            "contains space",
            "bang!",
        ],
    )
    def test_invalid_namespace_identifiers(self, ns: str) -> None:
        with pytest.raises(IdentifierValidationError):
            SubjectReference(namespace=NamespaceId(ns))

    def test_to_dict(self) -> None:
        s = SubjectReference(namespace=NamespaceId("user"), relation=Relation("member"))
        assert s.to_dict() == {
            "namespace": "user",
            "relation": "member",
            "wildcard": False,
        }

    def test_to_dict_with_wildcard(self) -> None:
        s = SubjectReference(namespace=NamespaceId("user"), wildcard=True)
        assert s.to_dict() == {
            "namespace": "user",
            "relation": None,
            "wildcard": True,
        }

    def test_roundtrip_dict(self) -> None:
        s = SubjectReference(namespace=NamespaceId("user"), relation=Relation("member"))
        assert SubjectReference.from_dict(s.to_dict()) == s

    def test_from_dict_missing_relation(self) -> None:
        data = {"namespace": "user", "wildcard": False}
        s = SubjectReference.from_dict(data)
        assert s.namespace.value == "user"
        assert s.relation is None
        assert s.wildcard is False

    def test_from_dict_missing_wildcard(self) -> None:
        data = {"namespace": "group", "relation": "member"}
        s = SubjectReference.from_dict(data)
        assert s.namespace.value == "group"
        assert s.relation is not None
        assert s.relation.value == "member"
        # Missing wildcard should behave as falsy (no wildcard)
        assert bool(s.wildcard) is False

    def test_from_dict_relation_none(self) -> None:
        data = {"namespace": "user", "relation": None}
        s = SubjectReference.from_dict(data)
        assert s.namespace.value == "user"
        assert s.relation is None
        assert bool(s.wildcard) is False

    def test_from_dict_conflicting_relation_and_wildcard_raises(self) -> None:
        data = {"namespace": "group", "relation": "member", "wildcard": True}
        with pytest.raises(
            ValueError, match="wildcard and relation are mutually exclusive"
        ):
            SubjectReference.from_dict(data)

    def test_accepts_str_namespace_only(self) -> None:
        s = SubjectReference(namespace="user")
        assert s.namespace.value == "user"
        assert s.relation is None
        assert s.wildcard is False

    def test_accepts_str_and_relation(self) -> None:
        s = SubjectReference(namespace="group", relation=Relation("member"))
        assert s.namespace.value == "group"
        assert s.relation is not None
        assert s.relation.value == "member"
        assert s.wildcard is False

    def test_accepts_str_relation(self) -> None:
        s = SubjectReference(namespace="group", relation="member")
        assert s.namespace.value == "group"
        assert s.relation is not None
        assert s.relation.value == "member"

    def test_allows_respects_wildcard_semantics(self) -> None:
        direct = SubjectReference(namespace="user")
        wildcard = SubjectReference(namespace="user", wildcard=True)
        userset = SubjectReference(namespace="group", relation="member")

        assert direct.allows(namespace="user", entity_id="alice", relation=None)
        assert not direct.allows(namespace="user", entity_id="*", relation=None)
        assert wildcard.allows(namespace="user", entity_id="*", relation=None)
        assert not wildcard.allows(namespace="user", entity_id="alice", relation=None)
        assert userset.allows(namespace="group", entity_id="eng", relation="member")
        assert not userset.allows(namespace="group", entity_id="*", relation="member")

    def test_normalizes_and_equality(self) -> None:
        s_str = SubjectReference(namespace="user")
        s_ns = SubjectReference(namespace=NamespaceId("user"))
        assert s_str == s_ns
        assert hash(s_str) == hash(s_ns)

    @pytest.mark.parametrize(
        "ns",
        [
            "",
            "1starts_with_digit",
            "contains space",
            "bang!",
        ],
    )
    def test_invalid_namespace_identifiers_with_str(self, ns: str) -> None:
        with pytest.raises(IdentifierValidationError):
            SubjectReference(namespace=ns)
