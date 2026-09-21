import logging
from pathlib import Path

from ..config import settings
from ..domain import AnalysisJob, ChangedFile, ChangeSet, PullRequestChanges
from ..workspace import WorkspaceSecurityError, run_git_output, validate_branch
from .diff_parser import NumstatEntry, PatchFile, parse_numstat, parse_patch
from .generated_files import is_generated

logger = logging.getLogger(__name__)

# The workspace clones the head branch with depth 50 (git_client.py). The
# target branch is fetched to the same depth first; if the two histories
# don't meet within it, both are fetched deeper. Most PRs branch off
# within a few dozen commits, so the first attempt nearly always wins.
_FETCH_DEPTHS = (50, 250, 1000)

# Above this many changed lines (after generated files are removed) the
# per-line patch isn't parsed at all: nothing downstream reviews a PR this
# big line by line, and parsing it would only cost memory.
MAX_LINE_LEVEL_CHANGES = 20_000

# Every diff call passes these. --no-ext-diff and --no-textconv make sure
# nothing in the repository's own .gitattributes can change what git runs
# or prints; core.quotePath=false keeps non-ASCII names unescaped.
_DIFF_FLAGS = ["--no-color", "--no-ext-diff", "--no-textconv", "-M"]


class DiffExtractor:
    """
    Works out what a pull request changed: `git diff <merge-base> <head>`,
    where the merge base is the commit the PR branched off the target
    branch.

    Diffing against the merge base rather than the target branch's tip is
    what makes this the PR's own change. If the target branch has moved on
    since the PR branched, a tip-to-tip diff would also show every commit
    merged there since — work this PR didn't do.

    Never raises for an expected situation (no target branch, histories
    too far apart, git failure): it returns PullRequestChanges with status
    "unavailable" and a reason, because linter analysis must still
    complete without it.
    """

    def __init__(self, timeout: float | None = None, fetch_depths: tuple[int, ...] = _FETCH_DEPTHS):
        self._timeout = timeout if timeout is not None else settings.git_clone_timeout_seconds
        self._fetch_depths = fetch_depths

    async def extract(self, workspace_path: Path, job: AnalysisJob,
                      git_env: dict[str, str] | None = None) -> PullRequestChanges:
        """`git_env`: the clone's credentials, needed to fetch a private repository's target branch."""
        if not job.target_branch:
            return _unavailable("no_target_branch")

        try:
            validate_branch(job.target_branch)
            base_sha = await self._find_merge_base(workspace_path, job, git_env)
            if base_sha is None:
                return _unavailable("no_merge_base")

            head_sha = (await self._git(workspace_path, ["rev-parse", "HEAD"])).strip()
            return await self._diff(workspace_path, job.target_branch, base_sha, head_sha)

        except WorkspaceSecurityError as error:
            logger.warning("[job:%s] could not compute PR changes: %s", job.job_id, error)
            return _unavailable("error")

    # -------------------------------------------------------------------

    async def _find_merge_base(self, workspace: Path, job: AnalysisJob,
                               git_env: dict[str, str] | None = None) -> str | None:
        target_ref = f"refs/remotes/origin/{job.target_branch}"
        # "--" keeps the refspec from being read as an option, as in
        # clone_commit. The refspec names a local ref so merge-base can use it.
        target_refspec = f"+refs/heads/{job.target_branch}:{target_ref}"

        for attempt, depth in enumerate(self._fetch_depths):
            await self._git(workspace, ["fetch", "--depth", str(depth), "origin", "--", target_refspec], git_env)
            if attempt > 0:
                # Deepen the head side too: the common ancestor may be
                # outside the head branch's original 50 commits.
                await self._git(workspace, ["fetch", "--depth", str(depth), "origin", "--", job.branch], git_env)

            code, output = await run_git_output(
                ["merge-base", "HEAD", target_ref], workspace, self._timeout, allowed_exit_codes=(0, 1)
            )
            if code == 0:
                return output.decode().strip()

        return None

    async def _diff(self, workspace: Path, target_branch: str, base_sha: str, head_sha: str) -> PullRequestChanges:
        numstat = parse_numstat(await self._git_bytes(
            workspace,
            ["-c", "core.quotePath=false", "diff", "--numstat", "-z", *_DIFF_FLAGS, base_sha, head_sha],
        ))

        included = [entry for entry in numstat if not is_generated(entry.path)]
        excluded = sorted(entry.path for entry in numstat if is_generated(entry.path))
        changed_lines = sum((e.lines_added or 0) + (e.lines_removed or 0) for e in included)

        patch: dict[str, PatchFile] = {}
        line_level = changed_lines <= MAX_LINE_LEVEL_CHANGES

        if line_level and included:
            # Excluding generated files here too means a 10,000-line
            # lockfile never even reaches this process.
            exclude_specs = [f":(exclude,literal){path}" for path in excluded]
            patch = parse_patch(await self._git_bytes(
                workspace,
                ["-c", "core.quotePath=false", "diff", "--unified=0", *_DIFF_FLAGS,
                 base_sha, head_sha, "--", ".", *exclude_specs],
            ))

        files = [_to_changed_file(entry, patch.get(entry.path)) for entry in included]
        change_set = ChangeSet(
            base_sha=base_sha,
            head_sha=head_sha,
            target_branch=target_branch,
            files=files,
            excluded_files=excluded,
        )

        return PullRequestChanges(
            status="available" if line_level else "unavailable",
            unavailable_reason=None if line_level else "too_large",
            change_set=change_set,
            files_changed=len(files),
            lines_added=change_set.lines_added,
            lines_removed=change_set.lines_removed,
        )

    async def _git(self, workspace: Path, args: list[str], env: dict[str, str] | None = None) -> str:
        return (await self._git_bytes(workspace, args, env)).decode("utf-8", errors="replace")

    async def _git_bytes(self, workspace: Path, args: list[str], env: dict[str, str] | None = None) -> bytes:
        _code, output = await run_git_output(args, workspace, self._timeout, env=env)
        return output


def _to_changed_file(entry: NumstatEntry, patch: PatchFile | None) -> ChangedFile:
    status = patch.status if patch else ("renamed" if entry.old_path else "modified")
    return ChangedFile(
        path=entry.path,
        old_path=entry.old_path,
        status=status,
        is_binary=entry.is_binary,
        lines_added=entry.lines_added or 0,
        lines_removed=entry.lines_removed or 0,
        added_lines=sorted(set(patch.added_lines)) if patch else [],
        deletion_points=sorted(set(patch.deletion_points)) if patch else [],
    )


def _unavailable(reason: str) -> PullRequestChanges:
    return PullRequestChanges(status="unavailable", unavailable_reason=reason)
