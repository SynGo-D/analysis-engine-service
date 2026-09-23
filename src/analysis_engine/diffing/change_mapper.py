from ..domain import ChangedFile, ChangedSymbol, ChangeSet, Finding
from ..domain.code_index import RepoIndex, Symbol

# Stored in Finding.metadata rather than as a new column: it describes the
# finding's relation to one pull request, not the finding itself, and the
# metadata JSONB already round-trips through storage and the API.
ON_CHANGED_LINE = "on_changed_line"


def mark_findings(findings: list[Finding], change_set: ChangeSet) -> tuple[list[Finding], int]:
    """
    Tags every finding with whether it sits on a line this PR changed.

    A finding without a line (some of Radon's per-file results) counts if
    its file's content changed at all: the PR plausibly moved that
    file-level number. A pure rename didn't change content, so it doesn't.

    Returns new Finding objects (the inputs are left untouched) and how
    many are on changed lines.
    """
    changed = {f.path: (f, set(f.added_lines)) for f in change_set.files if f.status != "deleted"}
    marked: list[Finding] = []
    count = 0

    for finding in findings:
        on_changed = _is_on_changed_line(finding, changed)
        count += on_changed
        marked.append(finding.model_copy(update={"metadata": {**finding.metadata, ON_CHANGED_LINE: on_changed}}))

    return marked, count


def changed_symbols(index: RepoIndex, change_set: ChangeSet) -> list[ChangedSymbol]:
    """
    The functions, methods and classes this PR changed.

    For each changed line, only the *innermost* symbol containing it is
    kept: editing a method changes that method, and listing its class too
    would double the context an agent is given about the same edit. A
    class appears only when a line directly in its body (not inside one of
    its methods) changed.
    """
    found: dict[str, Symbol] = {}

    for changed_file in change_set.files:
        if changed_file.status == "deleted":
            continue

        lines = set(changed_file.added_lines) | set(changed_file.deletion_points)
        symbols = index.symbols_in_file(changed_file.path)

        for line in lines:
            containing = [s for s in symbols if s.contains_line(line)]
            if containing:
                innermost = min(containing, key=lambda s: s.end_line - s.start_line)
                found[innermost.symbol_id] = innermost

    ordered = sorted(found.values(), key=lambda s: (s.file_path, s.start_line))
    return [
        ChangedSymbol(
            symbol_id=symbol.symbol_id,
            name=symbol.name,
            qualified_name=symbol.qualified_name,
            kind=symbol.kind,
            file_path=symbol.file_path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            callers_count=len(index.callers_of(symbol.symbol_id)),
        )
        for symbol in ordered
    ]


def _is_on_changed_line(finding: Finding, changed: dict[str, tuple[ChangedFile, set[int]]]) -> bool:
    entry = changed.get(finding.file_path)
    if entry is None:
        return False
    changed_file, added = entry

    if finding.line is None:
        return bool(changed_file.added_lines or changed_file.deletion_points)

    # A multi-line finding (Pylint reports ranges) counts if any line of
    # its range changed.
    end = finding.end_line if finding.end_line and finding.end_line >= finding.line else finding.line
    return any(line in added for line in range(finding.line, end + 1))
