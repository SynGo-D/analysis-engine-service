import re
from dataclasses import dataclass, field

from ..domain import ChangeStatus

# "@@ -12,3 +14,0 @@" — a count is omitted when it is 1.
_HUNK_HEADER = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_QUOTED_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


@dataclass
class NumstatEntry:
    """One line of `git diff --numstat -z`. Counts are None for binary files."""

    path: str
    old_path: str | None
    lines_added: int | None
    lines_removed: int | None

    @property
    def is_binary(self) -> bool:
        return self.lines_added is None


@dataclass
class PatchFile:
    """What one file's section of a `--unified=0` patch says about it."""

    path: str
    old_path: str | None = None
    status: ChangeStatus = "modified"
    is_binary: bool = False
    added_lines: list[int] = field(default_factory=list)
    deletion_points: list[int] = field(default_factory=list)


def parse_numstat(output: bytes) -> list[NumstatEntry]:
    """
    Parses `git diff --numstat -z -M`.

    With -z, paths are written raw rather than quoted, which is why this
    format is used for the file list: it handles any filename. A normal
    entry is "added<TAB>removed<TAB>path<NUL>". A rename leaves the path
    empty and follows it with "old<NUL>new<NUL>".
    """
    entries: list[NumstatEntry] = []
    fields = output.split(b"\0")
    i = 0

    while i < len(fields):
        record = fields[i]
        i += 1
        if not record:
            continue

        added, removed, path = record.split(b"\t", 2)
        old_path: str | None = None

        if path == b"":
            old_path = _decode(fields[i])
            path = fields[i + 1]
            i += 2

        entries.append(NumstatEntry(
            path=_decode(path),
            old_path=old_path,
            lines_added=None if added == b"-" else int(added),
            lines_removed=None if removed == b"-" else int(removed),
        ))

    return entries


def parse_patch(output: bytes) -> dict[str, PatchFile]:
    """
    Parses `git diff --unified=0` into changed line numbers per file,
    keyed by the file's path in the new version (its old path for a
    deleted file).

    Zero context lines is what makes this exact: every "+" line is inside
    a hunk whose header states where it starts in the new file, so the
    added lines are just that range — no line-by-line counting of context.
    """
    files: dict[str, PatchFile] = {}
    current: PatchFile | None = None
    minus_path: str | None = None

    # Header lines (---, +++, rename, mode) only appear before a file's
    # first hunk. After that, a removed line whose content is "-- x" shows
    # up as "--- x" and must not be mistaken for a header.
    in_hunks = False

    for line in output.split(b"\n"):
        if line.startswith(b"diff --git "):
            current = PatchFile(path="")
            minus_path = None
            in_hunks = False
            continue
        if current is None:
            continue

        if line.startswith(b"@@"):
            in_hunks = True
            _apply_hunk(line, current)
        elif in_hunks:
            continue
        elif line.startswith(b"new file mode"):
            current.status = "added"
        elif line.startswith(b"deleted file mode"):
            current.status = "deleted"
        elif line.startswith(b"rename from "):
            current.status = "renamed"
            current.old_path = _unquote(line[len(b"rename from "):])
        elif line.startswith(b"rename to "):
            current.path = _unquote(line[len(b"rename to "):])
            files[current.path] = current
        elif line.startswith(b"Binary files "):
            current.is_binary = True
            _register_binary(line, current, files)
        elif line.startswith(b"--- "):
            minus_path = _strip_prefix(_unquote(line[4:]), "a/")
        elif line.startswith(b"+++ "):
            plus_path = _strip_prefix(_unquote(line[4:]), "b/")
            # A deleted file's new side is /dev/null; key it by its old path.
            current.path = minus_path if plus_path is None else plus_path
            if current.path:
                files[current.path] = current

    return files


def _apply_hunk(header: bytes, current: PatchFile) -> None:
    match = _HUNK_HEADER.match(header)
    if match is None:
        return

    new_start = int(match.group(3))
    new_count = 1 if match.group(4) is None else int(match.group(4))

    if new_count > 0:
        current.added_lines.extend(range(new_start, new_start + new_count))
    else:
        # Lines were only removed. git reports the new-file line *before*
        # the removal (0 at the top of the file), which lies inside the
        # same function in practice.
        current.deletion_points.append(max(new_start, 1))


def _register_binary(line: bytes, current: PatchFile, files: dict[str, PatchFile]) -> None:
    # "Binary files a/x and b/y differ" is the only place a binary file's
    # path appears when there are no ---/+++ lines.
    text = line.decode("utf-8", errors="replace")
    match = re.match(r"^Binary files (.+) and (.+) differ$", text)
    if match is None:
        return
    old, new = match.group(1), match.group(2)
    path = _strip_prefix(new.strip('"'), "b/") or _strip_prefix(old.strip('"'), "a/")
    if path:
        current.path = path
        files[path] = current


def _strip_prefix(path: str, prefix: str) -> str | None:
    if path == "/dev/null":
        return None
    return path[len(prefix):] if path.startswith(prefix) else path


def _unquote(raw: bytes) -> str:
    """
    Undoes git's C-style path quoting ("a/tab\\there.py"). Callers pass
    `-c core.quotePath=false`, so non-ASCII names arrive unquoted; this
    still has to handle the characters git always quotes (quotes,
    backslashes, control characters) and octal escapes.
    """
    # git appends a tab to ---/+++ names that contain a space, so tools
    # like `patch` can find where the name ends.
    raw = raw.rstrip(b"\r").rstrip(b"\t")
    if not (raw.startswith(b'"') and raw.endswith(b'"') and len(raw) >= 2):
        return _decode(raw)

    body = raw[1:-1].decode("latin-1")
    out = bytearray()
    i = 0
    while i < len(body):
        char = body[i]
        if char != "\\" or i + 1 >= len(body):
            out.extend(char.encode("latin-1"))
            i += 1
            continue
        nxt = body[i + 1]
        octal = body[i + 1:i + 4]
        if len(octal) == 3 and all(c in "01234567" for c in octal):
            out.append(int(octal, 8))
            i += 4
        else:
            out.append(_QUOTED_ESCAPES.get(nxt, ord(nxt)))
            i += 2
    return out.decode("utf-8", errors="replace")


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")
