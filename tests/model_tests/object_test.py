import pytest

from zanzipy.models import EntityId, IdentifierValidationError, NamespaceId, Obj


class TestObj:
    def test_construct_and_str(self) -> None:
        o = Obj(NamespaceId("document"), EntityId("readme"))
        assert str(o) == "document:readme"

    def test_equality_and_hash(self) -> None:
        a = Obj(NamespaceId("document"), EntityId("readme"))
        b = Obj(NamespaceId("document"), EntityId("readme"))
        c = Obj(NamespaceId("document"), EntityId("other"))

        assert a == b
        assert hash(a) == hash(b)
        assert a != c
        s = {a, b, c}
        assert len(s) == 2

    def test_from_string_and_str_round_trip(self) -> None:
        o = Obj.from_string("document:readme")
        assert isinstance(o, Obj)
        assert str(o) == "document:readme"

    def test_to_from_dict_round_trip(self) -> None:
        original = Obj(NamespaceId("document"), EntityId("readme"))
        as_dict = original.to_dict()
        restored = Obj.from_dict(as_dict)
        assert restored == original
        assert str(restored) == "document:readme"

    @pytest.mark.parametrize("ns", ["", "bad space", "1bad"])  # invalid namespaces
    def test_invalid_namespace(self, ns: str) -> None:
        with pytest.raises(IdentifierValidationError):
            Obj(NamespaceId(ns), EntityId("id"))
