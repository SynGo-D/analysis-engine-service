from pathlib import PurePosixPath

# Files nobody reviews line by line. Left out of the change set so they
# neither inflate a PR's size (one lockfile update can be 10,000 lines)
# nor cost anything once the diff is sent to a model.
_LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lockb", "poetry.lock", "Pipfile.lock", "uv.lock", "Cargo.lock",
    "composer.lock", "Gemfile.lock", "go.sum",
}

# Same directory names the indexer and language detector already skip,
# plus common build-output and vendoring locations.
_GENERATED_DIRS = {
    "node_modules", "dist", "build", "coverage", ".next", "out",
    "vendor", "__pycache__", ".venv", "venv",
}

_GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".pyc", ".snap")


def is_generated(path: str) -> bool:
    """True for lockfiles, build output, vendored code and minified bundles."""
    posix = PurePosixPath(path)
    if posix.name in _LOCKFILES:
        return True
    if any(part in _GENERATED_DIRS for part in posix.parts[:-1]):
        return True
    return posix.name.endswith(_GENERATED_SUFFIXES)
