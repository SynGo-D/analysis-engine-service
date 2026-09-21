from pathlib import Path, PurePosixPath


class PathNotAllowed(ValueError):
    """A requested path points outside the workspace, or at git's internals."""


def resolve_in_workspace(workspace: Path, relative_path: str) -> Path:
    """
    Turns a path an agent asked for into a real path inside the workspace,
    or refuses.

    The path comes from model output, which in turn read the PR's code —
    so it is attacker-influenced. Three ways out are closed:

    - absolute paths and ".." segments;
    - symlinks: the repository can contain a link to /etc/passwd or to a
      file elsewhere on this host, so the path is resolved (following
      links) *before* the containment check, not after;
    - .git/: the workspace's git config holds the clone URL, which for a
      private repository can include an access token.
    """
    if not relative_path or "\0" in relative_path:
        raise PathNotAllowed("Empty or invalid path.")

    posix = PurePosixPath(relative_path.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts:
        raise PathNotAllowed(f"Path must be relative to the repository root: {relative_path}")

    root = workspace.resolve()
    resolved = (root / posix).resolve()

    if not resolved.is_relative_to(root):
        raise PathNotAllowed(f"Path leaves the repository: {relative_path}")

    inside = resolved.relative_to(root).parts
    if inside and inside[0] == ".git":
        raise PathNotAllowed("The .git directory is not readable.")

    return resolved
