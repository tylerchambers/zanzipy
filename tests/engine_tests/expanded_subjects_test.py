from zanzipy.engine.expanded_subjects import SubjectSet, _ExpandedSubjects


class TestExpandedSubjects:
    def test_wildcard_intersection_keeps_matching_finite_subjects(self) -> None:
        wildcard = _ExpandedSubjects.from_rendered_subjects({"user:*"})
        finite = _ExpandedSubjects.from_rendered_subjects(
            {"group:eng#member", "user:alice"}
        )

        result = wildcard.intersection(finite).to_subject_set()

        assert result.users == {"user:alice"}
        assert result.usersets == set()
        assert result.wildcard_exclusions == {}

    def test_wildcard_difference_preserves_finite_exceptions(self) -> None:
        wildcard = _ExpandedSubjects.from_rendered_subjects({"user:*"})
        banned = _ExpandedSubjects.from_rendered_subjects(
            {"group:eng#member", "user:alice"}
        )

        result = wildcard.difference(banned).to_subject_set()

        assert result.users == set()
        assert result.usersets == set()
        assert result.wildcard_exclusions == {"user:*": {"user:alice"}}

    def test_wildcard_difference_between_exceptions_materializes_finite_set(
        self,
    ) -> None:
        all_except_alice = _ExpandedSubjects.from_subject_set(
            SubjectSet(wildcard_exclusions={"user:*": {"user:alice"}})
        )
        all_except_bob = _ExpandedSubjects.from_subject_set(
            SubjectSet(wildcard_exclusions={"user:*": {"user:bob"}})
        )

        result = all_except_alice.difference(all_except_bob).to_subject_set()

        assert result.users == {"user:bob"}
        assert result.usersets == set()
        assert result.wildcard_exclusions == {}

    def test_union_with_finite_subject_removes_matching_exception(self) -> None:
        all_except_alice = _ExpandedSubjects.from_subject_set(
            SubjectSet(wildcard_exclusions={"user:*": {"user:alice"}})
        )
        alice = _ExpandedSubjects.from_rendered_subjects({"user:alice"})

        result = all_except_alice.union(alice).to_subject_set()

        assert result.users == {"user:*"}
        assert result.usersets == set()
        assert result.wildcard_exclusions == {}

    def test_non_user_wildcard_remains_userset_bucket(self) -> None:
        result = _ExpandedSubjects.from_rendered_subjects({"group:*"}).to_subject_set()

        assert result.users == set()
        assert result.usersets == {"group:*"}
        assert result.wildcard_exclusions == {}
