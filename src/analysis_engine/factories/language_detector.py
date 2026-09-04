from pathlib import Path

# Deliberately simple — file-extension matching, not a content-based
# classifier (e.g. linguist-style byte analysis). This service only
# analyzes JavaScript/TypeScript, so this map exists to answer one
# question: "does this workspace contain any JS/TS at all" (a PR touching
# only, say, a README shouldn't schedule an ESLint run).
_EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
}

# Vendored/generated directories skipped during detection — without this,
# a committed node_modules or dist would cause the analyzer to run against
# code the repository owner doesn't actually own/write.
_IGNORED_DIR_NAMES = {
    ".git", "node_modules", "dist", "build", "coverage",
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
