import pytest

from zanzipy.models import (
    EntityIdValidationError,
    IdentifierValidationError,
    SubjectValidationError,
)


@pytest.fixture
def valid_identifiers() -> list[str]:
    return [
        "owner",
        "can_read",
        "_internal",
        "User123",
        "auth-service",
    ]


@pytest.fixture
def invalid_identifiers() -> list[str]:
    # Chinese example invalid due to starting rule
    return [
        "",
        "1abc",
        "bad space",
        "bad:colon",
        "中文",
    ]


@pytest.fixture
def valid_entity_ids() -> list[str]:
    return [
        "readme",
        "user-123",
        "uuid_abc-def",
        "文档",
    ]


@pytest.fixture
def invalid_entity_ids() -> list[str]:
    return [
        "",
        "bad space",
        "re@adme",
        "re:adme",
        "re#adme",
    ]


@pytest.fixture
def valid_subject_strings() -> list[str]:
    return [
        "user:alice",
        "group:eng#member",
    ]


@pytest.fixture
def invalid_subject_strings_with_exc() -> list[tuple[str, type[Exception]]]:
    return [
        ("user", SubjectValidationError),
        ("user:", EntityIdValidationError),
        (":alice", IdentifierValidationError),
        ("user:al ice", EntityIdValidationError),
        ("user:alice#", SubjectValidationError),
    ]


@pytest.fixture
def canonical_tuple_string() -> str:
    return "document:readme#owner@user:alice"
