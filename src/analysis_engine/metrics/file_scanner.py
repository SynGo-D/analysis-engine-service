from pathlib import Path

from ..languages import EXTENSION_LANGUAGE_MAP, IGNORED_DIR_NAMES


def scan_source_files(workspace_path: Path, languages: frozenset[str]) -> dict[str, int]:
    """
    Maps every analyzed source file (relative POSIX path, matching
    Finding.file_path) to its line count, for the languages an analyzer
    actually ran against.

    Scoped to `languages` rather than counting every file in the
    repository: AnalysisMetrics.loc is the denominator of the density
    figures, so it has to describe the code that was analyzed. Counting a
    Java service's YAML and XML alongside its classes would quietly
    deflate every "issues per KLOC" number on the dashboard.

    Independent of the analyzers' own output, because a clean file with
    zero findings still needs a line count — and no analyzer reliably
    reports source text for files it had nothing to say about.

    The extension and ignore lists come from languages.py, the same
    place language detection reads them, so a language added there
    cannot be silently missing from the metrics.

    Must be called while the workspace still exists (i.e. inside
    WorkspaceManager.prepare()'s `async with` block) — the temp directory
    is removed as soon as that context exits.
    """
    extensions = {
        extension
        for extension, language in EXTENSION_LANGUAGE_MAP.items()
        if language in languages
    }

    if not extensions:
        return {}

    file_lines: dict[str, int] = {}

    for path in workspace_path.rglob("*"):
        if not path.is_file():
            continue

        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue

        if path.suffix.lower() not in extensions:
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        relative_path = path.relative_to(workspace_path).as_posix()
        file_lines[relative_path] = len(text.splitlines())

    return file_lines
