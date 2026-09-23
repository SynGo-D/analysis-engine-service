"""
Which file extensions belong to which language, and which directories are
never the repository's own code.

Deliberately at the package root rather than inside factories/ or
metrics/: both need it, and metrics/ cannot import factories/ — the
Python analyzer already imports metrics/, so that direction closes a
cycle. Keeping the maps here lets language detection and line counting
agree by construction instead of by a comment asking someone to keep two
copies in sync.
"""

# Deliberately simple — file-extension matching, not a content-based
# classifier (e.g. linguist-style byte analysis). This map answers one
# question per language: "does this workspace contain any of it at all"
# (a PR touching only a README shouldn't schedule an ESLint, PMD or
# Python run).
EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".py": "python",
    ".java": "java",
}

# Vendored, generated and build-output directories. Without this, a
# committed node_modules, dist, virtualenv or Maven target/ would be
# analyzed and counted as code the repository's authors wrote.
#
# analyzers/python/python_analyzer.py's own discover_python_files() uses
# the same idea at the file-discovery layer.
IGNORED_DIR_NAMES: set[str] = {
    ".git", "node_modules", "dist", "build", "coverage",
    ".venv", "venv", "env", "__pycache__",
    "target", ".mvn", ".gradle",
}
