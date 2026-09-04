from pathlib import Path

# Mirrors factories/language_detector.py's JS/TS extension set and ignored
# directories exactly — the file set this scans has to line up with what
# ESLint actually visits (tools/eslint/eslint.config.cjs's own `ignores`),
# since FileStatistic/loc figures are meant to describe "the code ESLint
# analyzed," not an independently-defined file set.
_JS_TS_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
_IGNORED_DIR_NAMES = {".git", "node_modules", "dist", "build", "coverage"}


def scan_js_ts_files(workspace_path: Path) -> dict[str, int]:
    """
    Maps every analyzed JS/TS file (relative POSIX path, matching
    Finding.file_path) to its line count. Independent of ESLint's own
    output — a clean file with zero findings still needs a LOC figure for
    AnalysisMetrics.loc / FileStatistic, and ESLint's JSON formatter
    doesn't reliably include source text for files with no messages.

    Must be called while the workspace still exists (i.e. inside
    WorkspaceManager.prepare()'s `async with` block) — the temp directory
    is removed as soon as that context exits.
    """
    file_lines: dict[str, int] = {}

    for path in workspace_path.rglob("*"):
        if not path.is_file():
            continue

        if any(part in _IGNORED_DIR_NAMES for part in path.parts):
            continue

        if path.suffix.lower() not in _JS_TS_EXTENSIONS:
            continue

        text = path.read_text(encoding="utf-8", errors="replace")
        relative_path = path.relative_to(workspace_path).as_posix()
        file_lines[relative_path] = len(text.splitlines())

    return file_lines
