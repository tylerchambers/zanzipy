from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from zanzipy.models import RelationTuple
from zanzipy.storage.repos.concrete.memory import InMemoryRelationRepository
from zanzipy.storage.repos.concrete.sqlalchemy import SQLAlchemyRelationRepository
from zanzipy.storage.repos.concrete.sqlite import SQLiteRelationRepository
from zanzipy.storage.revision import (
    ReadContext,
    Revision,
    TenantId,
    TupleMutation,
    WriteContext,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from zanzipy.storage.repos.abstract.relations import RelationRepository

TENANT = TenantId("default")


def _sqlalchemy_repo() -> SQLAlchemyRelationRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repo = SQLAlchemyRelationRepository(session_factory)
    repo.create_schema(engine)
    return repo


@pytest.mark.parametrize(
    "repo_factory",
    [
        pytest.param(InMemoryRelationRepository, id="memory"),
        pytest.param(SQLiteRelationRepository, id="sqlite"),
        pytest.param(_sqlalchemy_repo, id="sqlalchemy"),
    ],
)
def test_conflicting_same_tuple_mutations_are_rejected(
    repo_factory: Callable[[], RelationRepository],
) -> None:
    repo = repo_factory()
    try:
        relation_tuple = RelationTuple.from_string("document:doc1#viewer@user:alice")
        seed = repo.write(WriteContext(TENANT), (TupleMutation.touch(relation_tuple),))

        for mutations in (
            (TupleMutation.delete(relation_tuple), TupleMutation.touch(relation_tuple)),
            (TupleMutation.touch(relation_tuple), TupleMutation.delete(relation_tuple)),
        ):
            with pytest.raises(ValueError, match="conflicting tuple mutations"):
                repo.write(WriteContext(TENANT), mutations)

        assert repo.head_revision(TENANT) == seed.revision
        assert (
            repo.get(relation_tuple, context=ReadContext(TENANT, seed.revision))
            == relation_tuple
        )
        changes = list(repo.watch(TENANT, after=Revision(0)))
        assert [
            (change.relation_tuple, change.operation.value) for change in changes
        ] == [(relation_tuple, "write")]
    finally:
        repo.close()
