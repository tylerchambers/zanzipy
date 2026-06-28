from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

from zanzipy.models import Obj, Relation, RelationTuple, Subject, TupleFilter
from zanzipy.storage.repos.abstract.relations import RelationRepository
from zanzipy.storage.revision import (
    ReadContext,
    RelationshipChange,
    Revision,
    TenantId,
    TupleMutation,
    WriteContext,
    WriteResult,
)


class _RepositoryDefaults(RelationRepository):
    def __init__(self) -> None:
        self.forward_filters: list[TupleFilter] = []
        self.reverse_filters: list[TupleFilter] = []
        self.closed = False

    def write(
        self,
        context: WriteContext,
        mutations: Iterable[TupleMutation],
    ) -> WriteResult:
        raise NotImplementedError

    def head_revision(self, tenant: TenantId) -> Revision:
        return Revision(0)

    def get(
        self,
        key: RelationTuple,
        *,
        context: ReadContext,
    ) -> RelationTuple | None:
        return None

    def read(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        self.forward_filters.append(filter)
        return ()

    def read_reverse(
        self,
        filter: TupleFilter,
        *,
        context: ReadContext,
    ) -> Iterable[RelationTuple]:
        self.reverse_filters.append(filter)
        return ()

    def watch(
        self,
        tenant: TenantId,
        *,
        after: Revision,
    ) -> Iterator[RelationshipChange]:
        return iter(())

    def close(self) -> None:
        self.closed = True


class TestRelationRepositoryDefaults:
    def test_convenience_read_methods_build_expected_filters(self) -> None:
        repo = _RepositoryDefaults()
        context = ReadContext(TenantId("default"), Revision(0))

        assert (
            list(repo.by_object(Obj.from_string("document:doc1"), context=context))
            == []
        )
        assert (
            list(repo.by_subject(Subject.from_string("user:alice"), context=context))
            == []
        )
        assert list(repo.by_relation(Relation("viewer"), context=context)) == []

        assert repo.forward_filters == [
            TupleFilter.from_object(Obj.from_string("document:doc1")),
            TupleFilter.from_relation(Relation("viewer")),
        ]
        assert repo.reverse_filters == [
            TupleFilter.from_subject(Subject.from_string("user:alice")),
        ]

    def test_default_diagnostics_and_context_manager_close(self) -> None:
        repo = _RepositoryDefaults()

        assert repo.ping() is True
        assert repo.info() == {}
        with repo as entered:
            assert entered is repo

        assert repo.closed is True

    def test_protocol_accepts_structural_repository(self) -> None:
        repo: Any = _RepositoryDefaults()

        assert isinstance(repo, RelationRepository)
