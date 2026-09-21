import asyncio
from pathlib import Path, PurePosixPath

from ..domain import Finding
from ..domain.code_index import RepoIndex, Symbol
from ..diffing import ON_CHANGED_LINE, is_generated
from .path_guard import PathNotAllowed, resolve_in_workspace
from .redaction import redact
from .refs import finding_ref

# Every limit below exists for cost. A tool result becomes input tokens on
# the agent's next turn *and every turn after it*, because the whole
# conversation is resent each round. An unbounded result is paid for many
# times over.
READ_MAX_LINES = 300
SYMBOL_SOURCE_MAX_LINES = 400
MAX_LINE_CHARS = 400          # minified or generated lines are thousands long
MAX_FILE_BYTES = 2_000_000
FIND_SYMBOL_MAX = 20
CALLERS_MAX = 25
CALLEES_MAX = 50
SEARCH_MAX_MATCHES = 50
SEARCH_MIN_CHARS = 3
SEARCH_MAX_CHARS = 200
FINDINGS_MAX = 50
TESTS_MAX = 20
_SEARCH_PER_FILE = 5

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

_TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs"}


class ToolError(Exception):
    """A problem the agent caused and can fix (bad path, unknown id). Shown to it as text."""


class RetrievalTools:
    """
    The read-only view of one checked-out repository that agents get.

    Every method returns plain text, not JSON: it's what the model reads,
    and text is both cheaper in tokens and easier for it to quote from.
    Every result is size-limited, says when something was left out, and
    has likely credentials redacted.

    Nothing here writes, executes repository code or reaches the network.
    `search_code` runs `git grep` on the local checkout only.
    """

    def __init__(self, workspace: Path, index: RepoIndex, findings: list[Finding], timeout: float = 10.0):
        self._workspace = workspace
        self._index = index
        self._findings = findings
        self._timeout = timeout

    # -------------------------------------------------------------------
    # Files
    # -------------------------------------------------------------------

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        lines = self._read_lines(path)
        total = len(lines)
        if total == 0:
            return f"{path} is empty."

        start = max(1, start_line)
        if start > total:
            raise ToolError(f"{path} has only {total} lines.")
        requested_end = end_line if end_line is not None else start + READ_MAX_LINES - 1
        end = min(requested_end, total, start + READ_MAX_LINES - 1)

        header = f"{path} (lines {start}-{end} of {total})"
        note = ""
        # Say so whenever the file continues past what was returned and the
        # caller didn't explicitly ask to stop here.
        if end < total and (end_line is None or end_line > end):
            note = f"\n[Stopped at {READ_MAX_LINES} lines. Call again with start_line={end + 1} for more.]"
        return header + "\n" + numbered_lines(lines, start, end) + note

    def get_symbol_source(self, symbol_id: str) -> str:
        symbol = self._symbol(symbol_id)
        lines = self._read_lines(symbol.file_path)
        end = min(symbol.end_line, symbol.start_line + SYMBOL_SOURCE_MAX_LINES - 1, len(lines))

        header = f"{symbol.kind} {symbol.qualified_name} — {symbol.file_path}:{symbol.start_line}-{symbol.end_line}"
        note = ""
        if end < symbol.end_line:
            note = f"\n[Truncated at {SYMBOL_SOURCE_MAX_LINES} lines. Use read_file for the rest.]"
        return header + "\n" + numbered_lines(lines, symbol.start_line, end) + note

    # -------------------------------------------------------------------
    # The call graph
    # -------------------------------------------------------------------

    def find_symbol(self, name: str) -> str:
        name = name.strip()
        matches = (
            [s for s in self._index.symbols if s.qualified_name == name]
            if "." in name else self._index.find_by_name(name)
        )
        if not matches:
            return f"No function, method or class named '{name}' in the index."

        shown = sorted(matches, key=lambda s: (s.file_path, s.start_line))[:FIND_SYMBOL_MAX]
        lines = [_describe_symbol(s) for s in shown]
        return "\n".join(lines) + _more(len(matches), len(shown))

    def callers_of(self, symbol_id: str) -> str:
        symbol = self._symbol(symbol_id)
        sites = sorted(self._index.call_sites_of(symbol_id), key=lambda e: (e.file_path, e.line))
        if not sites:
            return (
                f"No known callers of {symbol.qualified_name}. The index only links calls whose "
                "target name is unique in the repository, so this doesn't prove it's unused — "
                f"use search_code('{symbol.name}') to check."
            )

        shown = sites[:CALLERS_MAX]
        lines = []
        for site in shown:
            caller = self._index.get(site.source_symbol_id)
            caller_name = caller.qualified_name if caller else site.source_symbol_id
            lines.append(f"{caller_name} calls it at {site.file_path}:{site.line}  [id: {site.source_symbol_id}]")
        return f"Callers of {symbol.qualified_name}:\n" + "\n".join(lines) + _more(len(sites), len(shown))

    def callees_of(self, symbol_id: str) -> str:
        symbol = self._symbol(symbol_id)
        edges = self._index.callees_of(symbol_id)
        if not edges:
            return f"{symbol.qualified_name} makes no calls the index recognised."

        shown = edges[:CALLEES_MAX]
        lines = []
        for edge in shown:
            target = self._index.get(edge.target_symbol_id) if edge.target_symbol_id else None
            if target:
                lines.append(f"line {edge.line}: {edge.target_name} → {_describe_symbol(target)}")
            else:
                lines.append(f"line {edge.line}: {edge.target_name} (not resolved: library, builtin or ambiguous name)")
        return f"Calls made by {symbol.qualified_name}:\n" + "\n".join(lines) + _more(len(edges), len(shown))

    def list_tests_for(self, symbol_id: str) -> str:
        symbol = self._symbol(symbol_id)
        found: dict[str, tuple[Symbol, int, bool]] = {}

        for edge in self._index.calls_by_name(symbol.name):
            if not is_test_file(edge.file_path):
                continue
            caller = self._index.get(edge.source_symbol_id)
            if caller and caller.symbol_id not in found:
                found[caller.symbol_id] = (caller, edge.line, edge.target_symbol_id == symbol.symbol_id)

        if not found:
            return f"No test code calls anything named '{symbol.name}'."

        entries = sorted(found.values(), key=lambda t: (t[0].file_path, t[1]))
        shown = entries[:TESTS_MAX]
        lines = [
            f"{caller.qualified_name} — {caller.file_path}:{line} "
            + ("(calls this symbol)" if resolved else "(calls something named this; check it's the same one)")
            for caller, line, resolved in shown
        ]
        return f"Tests that call {symbol.name}:\n" + "\n".join(lines) + _more(len(entries), len(shown))

    # -------------------------------------------------------------------
    # Search
    # -------------------------------------------------------------------

    async def search_code(self, text: str, path_glob: str | None = None) -> str:
        """
        Literal text search (never a regex: a hostile or careless pattern
        could otherwise run for minutes) over the repository's tracked
        files, via git grep.
        """
        if len(text) < SEARCH_MIN_CHARS:
            raise ToolError(f"Search text must be at least {SEARCH_MIN_CHARS} characters.")
        if len(text) > SEARCH_MAX_CHARS:
            raise ToolError(f"Search text must be at most {SEARCH_MAX_CHARS} characters.")

        pathspecs = [f":(glob){path_glob}"] if path_glob else ["."]
        args = [
            "git", "-c", "core.quotePath=false", "grep", "-n", "-I", "-F", "--no-color",
            f"--max-count={_SEARCH_PER_FILE}", "-e", text, "--", *pathspecs,
        ]
        process = await asyncio.create_subprocess_exec(
            *args, cwd=str(self._workspace),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise ToolError("Search timed out. Use a more specific text or a path_glob.")

        if process.returncode == 1:
            return f"No matches for '{text}'."
        if process.returncode != 0:
            raise ToolError("Search failed. Check that path_glob is a valid glob.")

        matches = [
            line for line in stdout.decode("utf-8", errors="replace").splitlines()
            if not is_generated(line.split(":", 1)[0])
        ]
        if not matches:
            return f"No matches for '{text}' outside generated files."

        shown = [_clip(line) for line in matches[:SEARCH_MAX_MATCHES]]
        note = _more(len(matches), len(shown))
        per_file: dict[str, int] = {}
        for line in matches:
            file_path = line.split(":", 1)[0]
            per_file[file_path] = per_file.get(file_path, 0) + 1
        if any(count >= _SEARCH_PER_FILE for count in per_file.values()):
            note += f"\n[At most {_SEARCH_PER_FILE} matches per file are listed.]"
        return redact("\n".join(shown)) + note

    # -------------------------------------------------------------------
    # Linter findings
    # -------------------------------------------------------------------

    def get_linter_findings(self, path: str | None = None, rule_id: str | None = None, changed_only: bool = False) -> str:
        selected = [
            f for f in self._findings
            if (path is None or f.file_path == path)
            and (rule_id is None or f.rule_id == rule_id)
            and (not changed_only or f.metadata.get(ON_CHANGED_LINE) is True)
        ]
        if not selected:
            return "No linter findings match."

        selected.sort(key=lambda f: (_SEVERITY_ORDER.get(f.severity, 3), f.file_path, f.line or 0))
        shown = selected[:FINDINGS_MAX]
        return "\n".join(format_finding(f) for f in shown) + _more(len(selected), len(shown))

    # -------------------------------------------------------------------

    def _symbol(self, symbol_id: str) -> Symbol:
        symbol = self._index.get(symbol_id)
        if symbol is None:
            raise ToolError(f"Unknown symbol id '{symbol_id}'. Use find_symbol to look one up.")
        return symbol

    def _read_lines(self, path: str) -> list[str]:
        try:
            resolved = resolve_in_workspace(self._workspace, path)
        except PathNotAllowed as error:
            raise ToolError(str(error)) from None

        if not resolved.is_file():
            raise ToolError(f"No such file: {path}")
        if resolved.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(f"{path} is too large to read. Use search_code to find the part you need.")

        data = resolved.read_bytes()
        if b"\0" in data[:8192]:
            raise ToolError(f"{path} is a binary file.")
        return data.decode("utf-8", errors="replace").splitlines()


def format_finding(finding: Finding) -> str:
    """One compact line per finding — shared with the context pack so both read the same."""
    location = f"{finding.file_path}:{finding.line}" if finding.line else finding.file_path
    return f"[{finding_ref(finding)}] {finding.severity} {finding.tool}/{finding.rule_id} {location} — {_clip(finding.message)}"


def is_test_file(path: str) -> bool:
    posix = PurePosixPath(path)
    name = posix.name
    return (
        any(part in _TEST_DIR_NAMES for part in posix.parts[:-1])
        or name.startswith("test_")
        or name.endswith(("_test.py", "_test.go"))
        or ".test." in name
        or ".spec." in name
    )


def _describe_symbol(symbol: Symbol) -> str:
    return f"{symbol.kind} {symbol.qualified_name} — {symbol.file_path}:{symbol.start_line}-{symbol.end_line}  [id: {symbol.symbol_id}]"


def numbered_lines(lines: list[str], start: int, end: int) -> str:
    """Lines start..end (1-based, inclusive) as '  12| code', clipped and redacted."""
    return redact("\n".join(f"{n:>5}| {_clip(lines[n - 1])}" for n in range(start, end + 1)))


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " …[line truncated]"


def _more(total: int, shown: int) -> str:
    return f"\n[{total - shown} more not shown. Narrow the request to see them.]" if total > shown else ""
