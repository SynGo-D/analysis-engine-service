from typing import Literal

from pydantic import BaseModel, Field

ChangeStatus = Literal["added", "modified", "deleted", "renamed"]

# Why a pull request's changes couldn't be worked out. Each is a normal
# outcome, not an error: linter analysis still completes without them.
ChangesUnavailableReason = Literal[
    "no_target_branch",   # the job predates targetBranch, or the provider omitted it
    "no_merge_base",      # no common ancestor within the fetched history
    "too_large",          # too many changed lines to parse per line
    "error",              # git failed (network, timeout, rejected ref)
]


class ChangedFile(BaseModel):
    """
    One file the pull request touches, as seen by `git diff <merge-base> <head>`.

    Line numbers refer to the head (new) version of the file — the version
    the linters analyzed and the index was built from, so they line up with
    Finding.line and Symbol.start_line/end_line directly.
    """

    path: str
    old_path: str | None = Field(default=None, description="Set only when status is 'renamed'")
    status: ChangeStatus
    is_binary: bool = False

    lines_added: int = 0
    lines_removed: int = 0

    # Sorted, 1-based. Lines that exist in the new version and were added
    # or modified by this pull request.
    added_lines: list[int] = Field(default_factory=list)

    # A pure deletion leaves no new line behind, but it still changes the
    # function it happened in. These are the new-version lines the
    # deletions sit next to, so the change can still be mapped to a
    # symbol. Deliberately kept apart from added_lines: a linter finding
    # on a line *next to* a deletion is not a finding on a changed line.
    deletion_points: list[int] = Field(default_factory=list)


class ChangeSet(BaseModel):
    """What a pull request changes, relative to the branch it targets."""

    base_sha: str = Field(description="Merge base of the head commit and the target branch")
    head_sha: str
    target_branch: str

    files: list[ChangedFile] = Field(default_factory=list)

    # Changed but left out of `files` — lockfiles, build output, vendored
    # code, minified bundles. Nobody reviews these line by line, and
    # counting them would make ordinary PRs look huge.
    excluded_files: list[str] = Field(default_factory=list)

    @property
    def lines_added(self) -> int:
        return sum(f.lines_added for f in self.files)

    @property
    def lines_removed(self) -> int:
        return sum(f.lines_removed for f in self.files)

    def file(self, path: str) -> ChangedFile | None:
        return next((f for f in self.files if f.path == path), None)


class ChangedSymbol(BaseModel):
    """A function, method or class the pull request changed."""

    symbol_id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    # How many known call sites this symbol has elsewhere — the size of
    # the change's blast radius. A lower bound: the index only resolves
    # calls whose target name is unambiguous (see domain/code_index.py).
    callers_count: int = 0


class PullRequestChanges(BaseModel):
    """
    The pull-request view of an analysis: which files, lines and symbols
    changed, and how many linter findings fall on changed lines.

    Stored alongside the repository-wide AnalysisResult rather than
    replacing it — the metrics stay repository-wide, and this is what lets
    a client narrow them to "what this PR did".
    """

    status: Literal["available", "unavailable"]
    unavailable_reason: ChangesUnavailableReason | None = None

    change_set: ChangeSet | None = None
    changed_symbols: list[ChangedSymbol] = Field(default_factory=list)

    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    findings_on_changed_lines: int = 0
