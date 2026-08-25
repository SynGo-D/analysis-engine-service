import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse

from ..config import settings

# Only these providers are ever cloned from. Not a technical git
# limitation — a deliberate allowlist to close SSRF-style abuse: git
# itself supports file://, ssh://, and arbitrary hosts, any of which
# could be used to reach internal resources (e.g. a cloud metadata
# endpoint) if this validation weren't here.
_ALLOWED_HOSTS = {"github.com", "gitlab.com"}

_COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")

# Real bug, found via a live webhook delivery for a branch named
# "branch#1": the original validate_branch used an allowlist
# (^[A-Za-z0-9._/-]+$) that rejected '#' — but '#' is a perfectly valid
# character in real git branch names (git doesn't restrict it, neither
# does GitHub), so that allowlist was simply wrong, not conservative.
# Since every git subprocess call here uses argument-list execution
# (shell=False — see _run_git), shell metacharacters like '#' were never
# actually an injection risk to begin with; treating them as one was
# overcautious in a way that broke legitimate input. Replaced with a
# blocklist matching git's own actual ref-name rules (git-check-ref-format)
# instead of an arbitrary narrow allowlist.
_BRANCH_INVALID_CHARS = re.compile(r"[\x00-\x1f\x7f ~^:?*\[\\]")


class WorkspaceSecurityError(Exception):
    """
    Raised when job input fails validation before it's allowed anywhere
    near a subprocess or the filesystem, or when a git operation itself
    fails/times out. Repository source data (clone_url, commit_sha,
    branch) originates from the webhook payload — untrusted input by the
    time it reaches this service, several hops upstream.
    """


def validate_clone_url(clone_url: str) -> None:
    parsed = urlparse(clone_url)

    if parsed.scheme != "https":
        raise WorkspaceSecurityError(
            f"Rejected clone URL: scheme must be https, got '{parsed.scheme}'."
        )

    if parsed.hostname not in _ALLOWED_HOSTS:
        raise WorkspaceSecurityError(
            f"Rejected clone URL: host '{parsed.hostname}' is not an allowed provider."
        )


def validate_commit_sha(commit_sha: str) -> None:
    if not _COMMIT_SHA_PATTERN.match(commit_sha):
        raise WorkspaceSecurityError(
            f"Rejected commit SHA: does not look like a valid hex SHA: '{commit_sha}'."
        )


def validate_branch(branch: str) -> None:
    if not branch:
        raise WorkspaceSecurityError("Rejected branch name: empty.")

    # A branch name starting with '-' is git's own well-known
    # flag-injection vector — e.g. "--upload-pack=/bin/sh" could otherwise
    # be misread as an option rather than a ref. This is a real risk
    # regardless of shell=False, since it's git itself parsing argv.
    if branch.startswith("-"):
        raise WorkspaceSecurityError(
            "Rejected branch name: must not start with '-' (flag-injection guard)."
        )

    if ".." in branch:
        raise WorkspaceSecurityError(f"Rejected branch name: contains '..': '{branch}'.")

    if branch.startswith("/") or branch.endswith("/") or "//" in branch:
        raise WorkspaceSecurityError(f"Rejected branch name: invalid slash placement: '{branch}'.")

    if branch.endswith(".") or branch.endswith(".lock"):
        raise WorkspaceSecurityError(f"Rejected branch name: invalid trailing characters: '{branch}'.")

    if "@{" in branch or branch == "@":
        raise WorkspaceSecurityError(f"Rejected branch name: contains '@{{' or is '@': '{branch}'.")

    if _BRANCH_INVALID_CHARS.search(branch):
        raise WorkspaceSecurityError(
            f"Rejected branch name: contains a control character or a git-reserved character "
            f"(space, ~, ^, :, ?, *, [, \\): '{branch}'."
        )


async def _run_git(args: list[str], cwd: Path, timeout: float) -> None:
    """
    Runs a git command via argument-list execution — never shell=True and
    never a string-interpolated command. This is what actually prevents
    shell injection: even a maliciously crafted argument is passed to git
    as one literal value, never interpreted by a shell.
    """
    process = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise WorkspaceSecurityError(f"git {' '.join(args)} timed out after {timeout}s.")

    if process.returncode != 0:
        raise WorkspaceSecurityError(
            f"git {' '.join(args)} failed (exit {process.returncode}): "
            f"{stderr.decode(errors='replace').strip()}"
        )


async def clone_commit(
    clone_url: str,
    commit_sha: str,
    branch: str,
    destination: Path,
    timeout: float | None = None,
    fetch_depth: int = 50,
) -> None:
    """
    Fetches `branch` (shallow, `fetch_depth` commits) and checks out the
    specific `commit_sha` from what was fetched.

    This is *not* a straight fetch-by-SHA (`git fetch origin -- <sha>`),
    and that's a deliberate correction, not the original design: tested
    directly against a real public GitHub repo, fetch-by-arbitrary-SHA
    failed outright ("couldn't find remote ref") — GitHub does not
    universally support fetching by raw SHA
    (`uploadpack.allowReachableSHA1InWant` is often disabled). Fetching
    the branch by name is what GitHub/GitLab reliably support instead.

    `fetch_depth` is intentionally more than 1: `commit_sha` is normally
    the branch tip at webhook time, but a few commits may have landed on
    the branch between the webhook firing and this job being processed —
    50 is a pragmatic safety margin, not a guarantee. If `commit_sha`
    still isn't within that window, checkout fails with a clear
    "pathspec did not match" error rather than silently analyzing the
    wrong commit; retry/depth-tuning policy is Phase 10's job, not this
    function's.
    """
    validate_clone_url(clone_url)
    validate_commit_sha(commit_sha)
    validate_branch(branch)

    effective_timeout = timeout if timeout is not None else settings.git_clone_timeout_seconds

    await _run_git(["init"], cwd=destination, timeout=effective_timeout)
    await _run_git(["remote", "add", "origin", clone_url], cwd=destination, timeout=effective_timeout)
    # "--" ends option parsing so branch can never be misread as a flag,
    # even though validate_branch already rejects a leading '-'.
    await _run_git(
        ["fetch", "--depth", str(fetch_depth), "origin", "--", branch],
        cwd=destination, timeout=effective_timeout,
    )
    # Same "--" reasoning for commit_sha here — ref before "--", not after
    # (checkout treats anything after "--" as a pathspec, not a ref).
    await _run_git(["checkout", commit_sha, "--"], cwd=destination, timeout=effective_timeout)
