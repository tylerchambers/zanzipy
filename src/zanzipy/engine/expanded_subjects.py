from dataclasses import dataclass, field

from zanzipy.models import Subject


@dataclass(frozen=True, slots=True)
class SubjectSet:
    """Result of expanding a relation: aggregated subjects.

    - users: direct user subjects in canonical string form (e.g., "user:alice")
    - usersets: subject-set anchors or non-user direct subjects
    - wildcard_exclusions: namespace wildcards with explicit finite exceptions
    """

    users: set[str] = field(default_factory=set)
    usersets: set[str] = field(default_factory=set)
    wildcard_exclusions: dict[str, set[str]] = field(default_factory=dict)

    def union(self, other: SubjectSet) -> SubjectSet:
        """Return the semantic union of two expanded subject sets."""
        return (
            _ExpandedSubjects.from_subject_set(self)
            .union(_ExpandedSubjects.from_subject_set(other))
            .to_subject_set()
        )

    def intersection(self, other: SubjectSet) -> SubjectSet:
        """Return the semantic intersection of two expanded subject sets."""
        return (
            _ExpandedSubjects.from_subject_set(self)
            .intersection(_ExpandedSubjects.from_subject_set(other))
            .to_subject_set()
        )

    def difference(self, other: SubjectSet) -> SubjectSet:
        """Return the semantic difference of two expanded subject sets."""
        return (
            _ExpandedSubjects.from_subject_set(self)
            .difference(_ExpandedSubjects.from_subject_set(other))
            .to_subject_set()
        )


@dataclass(frozen=True, slots=True)
class _ExpandedSubjects:
    """Materialized subjects with namespace-wildcard set semantics."""

    finite: set[str] = field(default_factory=set)
    wildcards: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def from_subject_set(cls, subject_set: SubjectSet) -> _ExpandedSubjects:
        return cls.from_rendered_subjects(
            subject_set.users | subject_set.usersets,
            subject_set.wildcard_exclusions,
        )

    @classmethod
    def from_rendered_subjects(
        cls,
        subjects: set[str],
        wildcard_exclusions: dict[str, set[str]] | None = None,
    ) -> _ExpandedSubjects:
        finite: set[str] = set()
        wildcards: dict[str, set[str]] = {}
        for rendered in subjects:
            namespace = cls._wildcard_namespace(rendered)
            if namespace is None:
                finite.add(rendered)
            else:
                wildcards.setdefault(namespace, set())

        for rendered, exclusions in (wildcard_exclusions or {}).items():
            namespace = cls._wildcard_namespace(rendered)
            if namespace is not None:
                wildcards.setdefault(namespace, set()).update(exclusions)

        return cls(finite=finite, wildcards=wildcards)._normalized()

    def union(self, other: _ExpandedSubjects) -> _ExpandedSubjects:
        finite = self.finite | other.finite
        wildcards: dict[str, set[str]] = {}
        for namespace in self.wildcards.keys() | other.wildcards.keys():
            left_exclusions = self.wildcards.get(namespace)
            right_exclusions = other.wildcards.get(namespace)
            if left_exclusions is not None and right_exclusions is not None:
                wildcards[namespace] = left_exclusions & right_exclusions
            elif left_exclusions is not None:
                wildcards[namespace] = left_exclusions - self._subjects_in_namespace(
                    other.finite,
                    namespace,
                )
            elif right_exclusions is not None:
                wildcards[namespace] = right_exclusions - self._subjects_in_namespace(
                    self.finite,
                    namespace,
                )

        return _ExpandedSubjects(finite=finite, wildcards=wildcards)._normalized()

    def intersection(self, other: _ExpandedSubjects) -> _ExpandedSubjects:
        finite = {
            rendered for rendered in self.finite if other.contains_concrete(rendered)
        } | {rendered for rendered in other.finite if self.contains_concrete(rendered)}
        wildcards = {
            namespace: self.wildcards[namespace] | other.wildcards[namespace]
            for namespace in self.wildcards.keys() & other.wildcards.keys()
        }
        return _ExpandedSubjects(finite=finite, wildcards=wildcards)._normalized()

    def difference(self, other: _ExpandedSubjects) -> _ExpandedSubjects:
        finite = {
            rendered
            for rendered in self.finite
            if not other.contains_concrete(rendered)
        }
        wildcards: dict[str, set[str]] = {}
        for namespace, exclusions in self.wildcards.items():
            other_exclusions = other.wildcards.get(namespace)
            if other_exclusions is None:
                wildcards[namespace] = exclusions | self._subjects_in_namespace(
                    other.finite,
                    namespace,
                )
            else:
                finite.update(
                    rendered
                    for rendered in other_exclusions - exclusions
                    if self._concrete_subject_namespace(rendered) == namespace
                )

        return _ExpandedSubjects(finite=finite, wildcards=wildcards)._normalized()

    def contains_concrete(self, rendered: str) -> bool:
        if rendered in self.finite:
            return True
        namespace = self._concrete_subject_namespace(rendered)
        return (
            namespace is not None
            and namespace in self.wildcards
            and rendered not in self.wildcards[namespace]
        )

    def to_subject_set(self) -> SubjectSet:
        users: set[str] = set()
        usersets: set[str] = set()
        wildcard_exclusions: dict[str, set[str]] = {}

        for rendered in self.finite:
            self._add_rendered_subject(rendered, users=users, usersets=usersets)

        for namespace, exclusions in self.wildcards.items():
            rendered = f"{namespace}:*"
            if exclusions:
                wildcard_exclusions[rendered] = set(exclusions)
            else:
                self._add_rendered_subject(rendered, users=users, usersets=usersets)

        return SubjectSet(
            users=users,
            usersets=usersets,
            wildcard_exclusions=wildcard_exclusions,
        )

    def _normalized(self) -> _ExpandedSubjects:
        finite = set(self.finite)
        wildcards = {
            namespace: {
                rendered
                for rendered in exclusions
                if self._concrete_subject_namespace(rendered) == namespace
            }
            for namespace, exclusions in self.wildcards.items()
        }
        for namespace, exclusions in wildcards.items():
            exclusions.difference_update(self._subjects_in_namespace(finite, namespace))

        finite = {
            rendered
            for rendered in finite
            if not self._wildcard_includes(wildcards, rendered)
        }
        return _ExpandedSubjects(finite=finite, wildcards=wildcards)

    @staticmethod
    def _wildcard_namespace(rendered: str) -> str | None:
        subject = Subject.from_string(rendered)
        if subject.relation is None and str(subject.id) == "*":
            return str(subject.namespace)
        return None

    @staticmethod
    def _concrete_subject_namespace(rendered: str) -> str | None:
        subject = Subject.from_string(rendered)
        if subject.relation is None and str(subject.id) != "*":
            return str(subject.namespace)
        return None

    @classmethod
    def _subjects_in_namespace(cls, subjects: set[str], namespace: str) -> set[str]:
        return {
            rendered
            for rendered in subjects
            if cls._concrete_subject_namespace(rendered) == namespace
        }

    @classmethod
    def _wildcard_includes(
        cls,
        wildcards: dict[str, set[str]],
        rendered: str,
    ) -> bool:
        namespace = cls._concrete_subject_namespace(rendered)
        return (
            namespace is not None
            and namespace in wildcards
            and rendered not in wildcards[namespace]
        )

    @staticmethod
    def _add_rendered_subject(
        rendered: str,
        *,
        users: set[str],
        usersets: set[str],
    ) -> None:
        subject = Subject.from_string(rendered)
        if subject.relation is None and str(subject.namespace) == "user":
            users.add(rendered)
        else:
            usersets.add(rendered)
