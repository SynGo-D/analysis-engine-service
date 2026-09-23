from dataclasses import dataclass, field


@dataclass(frozen=True)
class Expected:
    """
    A defect planted in a case, located by a snippet of the head version
    of the file rather than by line number: line numbers are easy to get
    wrong when writing a case, and a snippet keeps working when a case is
    edited.
    """

    file: str
    snippet: str
    what: str  # for the report: what the Reviewer should have noticed
    # Other places where reporting this same defect counts as finding it,
    # e.g. a changed signature *or* the caller it breaks.
    also: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Case:
    """
    One evaluation pull request: a small repository on `main`, the PR's
    changes on top, and what a good reviewer should report.

    A case with no `expected` is a *clean* PR: anything reported on it is
    a false alarm. Clean cases matter as much as buggy ones; a reviewer
    that flags everything would score perfect recall.
    """

    id: str
    title: str
    description: str
    base: dict[str, str]
    # Files the PR adds or changes (full new content); None deletes a file.
    head: dict[str, str | None]
    expected: tuple[Expected, ...] = field(default_factory=tuple)

    @property
    def is_clean(self) -> bool:
        return not self.expected
