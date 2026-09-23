from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from ..config import settings
from ..diffing import ON_CHANGED_LINE
from ..domain import AnalysisJob, ChangedSymbol, Finding, PullRequestChanges
from ..domain.code_index import RepoIndex
from ..retrieval import (
    PathNotAllowed,
    is_secret_file,
    format_finding,
    format_rule,
    is_test_file,
    numbered_lines,
    redact,
    resolve_in_workspace,
)
from ..workspace import WorkspaceSecurityError, run_git_output

# Roughly 4 characters per token for code and English — close enough to
# budget with, and it needs no tokenizer. The real count comes back from
# the provider with every call and is what gets recorded.
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class ContextLimits:
    """
    Character budgets per section (docs/agent-architecture.md §5.6).

    Together they cap the pack at about 110,000 characters — roughly
    28,000 tokens, or under a cent of input on the cheapest model. The
    diff gets the largest share because it *is* the change under review;
    everything else is supporting context the agent can fetch more of with
    its tools if it needs to.
    """

    pr_chars: int = 4_000
    diff_chars: int = 50_000
    diff_file_chars: int = 15_000
    symbols_chars: int = 35_000
    symbol_max_lines: int = 200
    max_symbols: int = 40
    callers_chars: int = 6_000
    callers_per_symbol: int = 10
    findings_chars: int = 12_000
    max_findings: int = 60
    repo_map_chars: int = 2_000
    rules_chars: int = 8_000
    max_rules: int = 30


class ContextSection(BaseModel):
    title: str
    body: str
    # What was left out, in words the agent can act on ("…use read_file").
    omitted: str | None = None


class ContextPack(BaseModel):
    """The bounded bundle of facts the Reviewer starts from."""

    sections: list[ContextSection] = Field(default_factory=list)

    def render(self) -> str:
        parts = []
        for section in self.sections:
            text = f"## {section.title}\n{section.body}"
            if section.omitted:
                text += f"\n[{section.omitted}]"
            parts.append(text)
        return "\n\n".join(parts)

    @property
    def char_count(self) -> int:
        return len(self.render())

    @property
    def estimated_tokens(self) -> int:
        return self.char_count // CHARS_PER_TOKEN


class ContextPackBuilder:
    """
    Assembles the context pack from facts Stage 1 already produced — the
    change set, the index, the linter findings — plus one extra git call
    for the readable diff.

    Every section is cut to its budget and says what was cut, so the agent
    knows to ask for the rest instead of assuming it saw everything. All
    text passes through redaction before it can reach a prompt.
    """

    def __init__(self, limits: ContextLimits | None = None, timeout: float | None = None):
        self._limits = limits or ContextLimits()
        self._timeout = timeout if timeout is not None else settings.git_clone_timeout_seconds

    async def build(
        self,
        workspace: Path,
        job: AnalysisJob,
        changes: PullRequestChanges,
        index: RepoIndex,
        findings: list[Finding],
        rules=None,
    ) -> ContextPack:
        if changes.change_set is None:
            raise ValueError("A context pack needs the pull request's change set.")

        sections = [
            self._pull_request(job, changes),
            self._rules(rules, changes),
            await self._diff(workspace, changes),
            self._changed_symbols(workspace, changes),
            self._callers(index, changes.changed_symbols),
            self._findings(findings),
            self._repo_map(workspace, index, changes),
        ]
        return ContextPack(sections=[s for s in sections if s is not None])

    # -------------------------------------------------------------------

    def _pull_request(self, job: AnalysisJob, changes: PullRequestChanges) -> ContextSection:
        change_set = changes.change_set
        description = (job.description or "").strip() or "(no description)"
        lines = [
            f"Title: {job.title or '(no title)'}",
            f"Branch: {job.branch} → {change_set.target_branch}",
            f"Size: {changes.files_changed} files, +{changes.lines_added}/-{changes.lines_removed} lines",
            "Description:",
            description,
        ]
        if change_set.excluded_files:
            lines.append(f"Generated files not shown: {', '.join(change_set.excluded_files[:20])}")

        body, cut = _cut(redact("\n".join(lines)), self._limits.pr_chars)
        return ContextSection(title="Pull request", body=body, omitted="Description truncated." if cut else None)

    def _rules(self, rules, changes: PullRequestChanges) -> ContextSection | None:
        """
        The business rules whose scope covers a file this PR changes. Placed
        right after the PR's intent: they're the standard the change is
        judged against, and the section generic reviewers never have.
        """
        if rules is None:
            return None
        applicable = rules.applicable(changes.change_set)
        notes = []
        if rules.modified_by_pr:
            notes.append("This PR edits the rules file. The rules below are the target branch's, "
                         "which is what the PR is reviewed against.")
        if not applicable:
            return None if not notes else ContextSection(title="Business rules", body="None apply to the changed files.",
                                                         omitted=notes[0])

        lines, used = [], 0
        for rule in applicable[: self._limits.max_rules]:
            text = format_rule(rule)
            if used + len(text) > self._limits.rules_chars:
                break
            lines.append(text)
            used += len(text) + 1
        if len(lines) < len(applicable):
            notes.append(f"{len(applicable) - len(lines)} more applicable rule(s) not shown. Use get_rule.")
        return ContextSection(title="Business rules", body=redact("\n".join(lines)),
                              omitted=" ".join(notes) or None)

    async def _diff(self, workspace: Path, changes: PullRequestChanges) -> ContextSection:
        change_set = changes.change_set
        secret = [f.path for f in change_set.files if is_secret_file(f.path)]
        exclude = [f":(exclude,literal){path}" for path in [*change_set.excluded_files, *secret]]
        try:
            _code, output = await run_git_output(
                ["-c", "core.quotePath=false", "diff", "--unified=3", "--no-color", "--no-ext-diff",
                 "--no-textconv", "-M", change_set.base_sha, change_set.head_sha, "--", ".", *exclude],
                workspace, self._timeout,
            )
        except WorkspaceSecurityError:
            return ContextSection(title="Diff", body="(The diff could not be produced. Use read_file.)")

        per_file = _split_diff(output.decode("utf-8", errors="replace"))
        # Source before tests, then smaller files first: when the budget
        # runs out, what gets dropped is the biggest and least central.
        per_file.sort(key=lambda item: (is_test_file(item[0]), len(item[1])))

        included, omitted, used = [], [], 0
        for path, text in per_file:
            text, cut = _cut(text, self._limits.diff_file_chars)
            if cut:
                text += f"\n[Rest of this file's diff omitted. Use read_file('{path}').]"
            if used + len(text) > self._limits.diff_chars:
                omitted.append(path)
                continue
            included.append(text)
            used += len(text)

        notes = []
        if omitted:
            notes.append(f"Diff not shown for {len(omitted)} file(s): {', '.join(omitted[:15])}. Use read_file.")
        if secret:
            notes.append(f"Credential files changed but never shown: {', '.join(secret[:10])}.")
        note = " ".join(notes) or None
        return ContextSection(title="Diff", body=redact("\n".join(included)) or "(no textual changes)", omitted=note)

    def _changed_symbols(self, workspace: Path, changes: PullRequestChanges) -> ContextSection | None:
        # A function the PR wrote from scratch is already shown whole in
        # the diff; repeating it here would pay for the same lines twice.
        # Only functions that were *edited* need their full source, because
        # the diff shows just the edited hunk and a few lines around it.
        added = {f.path: set(f.added_lines) for f in changes.change_set.files}
        symbols = [
            s for s in changes.changed_symbols
            if not added.get(s.file_path, set()).issuperset(range(s.start_line, s.end_line + 1))
        ]
        if not symbols:
            return None

        # Widest blast radius first: if the budget runs out, the functions
        # nothing else calls are the ones left for the tools.
        ordered = sorted(symbols, key=lambda s: (-s.callers_count, s.file_path, s.start_line))
        blocks, used, shown = [], 0, 0
        file_cache: dict[str, list[str]] = {}

        for symbol in ordered[: self._limits.max_symbols]:
            lines = _file_lines(workspace, symbol.file_path, file_cache)
            if lines is None:
                continue
            end = min(symbol.end_line, symbol.start_line + self._limits.symbol_max_lines - 1, len(lines))
            source = numbered_lines(lines, symbol.start_line, end)
            header = f"### {symbol.kind} {symbol.qualified_name} — {symbol.file_path}:{symbol.start_line}-{symbol.end_line}  [id: {symbol.symbol_id}]"
            if end < symbol.end_line:
                source += "\n[truncated — use get_symbol_source]"
            block = f"{header}\n{source}"
            if used + len(block) > self._limits.symbols_chars:
                break
            blocks.append(block)
            used += len(block)
            shown += 1

        note = None
        if shown < len(symbols):
            note = f"{len(symbols) - shown} more changed symbol(s) not shown. Use get_symbol_source with their ids."
        return ContextSection(title="Edited functions (full source)", body=redact("\n\n".join(blocks)), omitted=note)

    def _callers(self, index: RepoIndex, symbols: list[ChangedSymbol]) -> ContextSection | None:
        entries, used, truncated = [], 0, False
        for symbol in symbols:
            sites = index.call_sites_of(symbol.symbol_id)
            if not sites:
                continue
            listed = []
            for site in sites[: self._limits.callers_per_symbol]:
                caller = index.get(site.source_symbol_id)
                listed.append(f"  {caller.qualified_name if caller else site.source_symbol_id} at {site.file_path}:{site.line}")
            more = len(sites) - len(listed)
            entry = f"{symbol.qualified_name} is called by:\n" + "\n".join(listed) + (f"\n  …and {more} more" if more else "")
            if used + len(entry) > self._limits.callers_chars:
                truncated = True
                break
            entries.append(entry)
            used += len(entry)

        if not entries:
            return None
        return ContextSection(
            title="Callers of changed functions",
            body="\n".join(entries),
            omitted="More callers exist. Use callers_of." if truncated else
            "Only calls to uniquely named functions are known; use search_code to check others.",
        )

    def _findings(self, findings: list[Finding]) -> ContextSection:
        on_changed = [f for f in findings if f.metadata.get(ON_CHANGED_LINE) is True]
        if not on_changed:
            return ContextSection(title="Linter findings on changed lines", body="None.")

        order = {"error": 0, "warning": 1, "info": 2}
        on_changed.sort(key=lambda f: (order.get(f.severity, 3), f.file_path, f.line or 0))

        lines, used = [], 0
        for finding in on_changed[: self._limits.max_findings]:
            line = format_finding(finding)
            if used + len(line) > self._limits.findings_chars:
                break
            lines.append(line)
            used += len(line) + 1

        note = None
        if len(lines) < len(on_changed):
            note = f"{len(on_changed) - len(lines)} more findings on changed lines. Use get_linter_findings(changed_only=true)."
        return ContextSection(title="Linter findings on changed lines", body="\n".join(lines), omitted=note)

    def _repo_map(self, workspace: Path, index: RepoIndex, changes: PullRequestChanges) -> ContextSection:
        top_level = sorted(
            entry.name + ("/" if entry.is_dir() else "")
            for entry in workspace.iterdir()
            if not entry.name.startswith(".") and entry.name not in {"node_modules", "dist", "build", "venv", ".venv"}
        )
        lines = [f"Top level: {', '.join(top_level[:40])}", f"Indexed: {len(index.symbols)} symbols in {index.files_indexed} files"]

        for changed in changes.change_set.files:
            imports = sorted({e.target_name for e in index.imports_of_file(changed.path)})
            if imports:
                lines.append(f"{changed.path} imports: {', '.join(imports[:15])}")

        body, _cut_happened = _cut("\n".join(lines), self._limits.repo_map_chars)
        return ContextSection(title="Repository map", body=body)


# -----------------------------------------------------------------------


def _split_diff(text: str) -> list[tuple[str, str]]:
    """Splits a multi-file diff into (path, that file's diff) pairs."""
    files: list[tuple[str, str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("diff --git ") and current:
            files.append(_with_path(current))
            current = []
        current.append(line)
    if current:
        files.append(_with_path(current))
    return files


def _with_path(lines: list[str]) -> tuple[str, str]:
    path = ""
    for line in lines[:8]:
        if line.startswith("+++ b/"):
            path = line[6:].rstrip("\t")
        elif line.startswith("rename to "):
            path = line[len("rename to "):]
        elif line.startswith("--- a/") and not path:
            path = line[6:].rstrip("\t")
    if not path and lines:
        path = lines[0].rsplit(" b/", 1)[-1]
    return path, "\n".join(lines)


def _file_lines(workspace: Path, path: str, cache: dict[str, list[str]]) -> list[str] | None:
    if path not in cache:
        try:
            # Same guard as the tools: a symlink in the repository must not
            # pull a file from elsewhere on this host into a prompt.
            resolved = resolve_in_workspace(workspace, path)
            cache[path] = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, PathNotAllowed):
            return None
    return cache[path]


def _cut(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit].rsplit("\n", 1)[0], True
