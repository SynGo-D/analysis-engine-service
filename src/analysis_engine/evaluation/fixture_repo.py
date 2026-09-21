import os
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from ..domain import AnalysisJob
from ..workspace import Workspace
from .case import Case

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "eval", "GIT_AUTHOR_EMAIL": "eval@codepulse.invalid",
    "GIT_COMMITTER_NAME": "eval", "GIT_COMMITTER_EMAIL": "eval@codepulse.invalid",
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, **_GIT_ENV, "GIT_CONFIG_GLOBAL": "/dev/null"},
    ).stdout


def _write(repo: Path, files: dict[str, str | None]) -> None:
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


class CaseCheckout:
    """
    Builds a case as a real git history — `main` with the base files, a
    `feature` branch with the PR's changes — and checks `feature` out the
    way the engine's own clone does (shallow fetch from a file:// remote).

    Used as the orchestrator's workspace manager, so everything
    downstream (diff, index, linters, context pack, review) runs exactly as
    it would for a PR from GitHub.
    """

    def __init__(self, case: Case):
        self.case = case
        self._root = Path(tempfile.mkdtemp(prefix=f"eval-{case.id}-"))
        self.origin = self._root / "origin"
        self.checkout = self._root / "checkout"
        self._build()

    def _build(self) -> None:
        self.origin.mkdir()
        _git(self.origin, "init", "-q", "-b", "main")
        _write(self.origin, self.case.base)
        _git(self.origin, "add", "-A")
        _git(self.origin, "commit", "-q", "-m", "base")
        _git(self.origin, "checkout", "-q", "-b", "feature")
        _write(self.origin, self.case.head)
        _git(self.origin, "add", "-A")
        _git(self.origin, "commit", "-q", "-m", self.case.title)
        self.head_sha = _git(self.origin, "rev-parse", "HEAD").strip()

        self.checkout.mkdir()
        _git(self.checkout, "init", "-q")
        _git(self.checkout, "remote", "add", "origin", f"file://{self.origin}")
        _git(self.checkout, "fetch", "-q", "--depth", "50", "origin", "--", "feature")
        _git(self.checkout, "checkout", "-q", "FETCH_HEAD")

    def job(self) -> AnalysisJob:
        return AnalysisJob(
            provider="github", repository=f"eval/{self.case.id}",
            clone_url=f"https://github.com/eval/{self.case.id}.git",
            commit_sha=self.head_sha, branch="feature", pull_request_number=1,
            queued_at="evaluation", target_branch="main",
            title=self.case.title, description=self.case.description,
        )

    @asynccontextmanager
    async def prepare(self, job: AnalysisJob):
        yield Workspace(job_id=job.job_id, path=self.checkout)

    def cleanup(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)
