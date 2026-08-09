from pathlib import Path

# Deliberately simple — file-extension matching, not a content-based
# classifier (e.g. linguist-style byte analysis). Matches "do not
# over-engineer": this correctly identifies the languages the initial
# tool set (ESLint, Pylint, Radon, Cppcheck) cares about, and a
# wrong/missing extension just means that one file isn't analyzed, not a
# correctness or security problem for the pipeline.
_EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".py": "python",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
}

# Vendored/generated directories skipped during detection — without this,
# a committed node_modules or venv would cause analyzers to run against
# code the repository owner doesn't actually own/write.
_IGNORED_DIR_NAMES = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache",
}


def detect_languages(workspace_path: Path) -> frozenset[str]:
    """
    Walks the workspace and returns the set of languages present, based
    on file extensions.
    """
    detected: set[str] = set()

    for path in workspace_path.rglob("*"):
        if not path.is_file():
            continue

        if any(part in _IGNORED_DIR_NAMES for part in path.parts):
            continue

        language = _EXTENSION_LANGUAGE_MAP.get(path.suffix.lower())
        if language:
            detected.add(language)

    return frozenset(detected)
