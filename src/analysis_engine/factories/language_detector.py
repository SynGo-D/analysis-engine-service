from pathlib import Path

from ..languages import EXTENSION_LANGUAGE_MAP, IGNORED_DIR_NAMES


def detect_languages(workspace_path: Path) -> frozenset[str]:
    """
    Walks the workspace and returns the set of languages present, based
    on file extensions.
    """
    detected: set[str] = set()

    for path in workspace_path.rglob("*"):
        if not path.is_file():
            continue

        if any(part in IGNORED_DIR_NAMES for part in path.parts):
            continue

        language = EXTENSION_LANGUAGE_MAP.get(path.suffix.lower())
        if language:
            detected.add(language)

    return frozenset(detected)
