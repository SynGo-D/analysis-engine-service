import asyncio
import logging
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from ..domain import AnalysisJob
from .credentials import RepositoryCredentials, git_auth_env
from .git_client import WorkspaceSecurityError, clone_commit

logger = logging.getLogger(__name__)


class Workspace:
    """An isolated, per-job filesystem workspace. Never reused across jobs."""

    def __init__(self, job_id: UUID, path: Path, git_env: dict[str, str] | None = None):
        self.job_id = job_id
        self.path = path
        # Credentials for this repository's host, for later git network
        # calls on the same checkout (fetching the PR's target branch).
        # Memory only: never written to the checkout.
        self.git_env = git_env or {}

    def __repr__(self) -> str:
        # Never print git_env: it holds a token.
        return f"Workspace(job_id={self.job_id}, path={self.path})"


class WorkspaceManager:
    """
    Creates an isolated temporary workspace per analysis job and
    guarantees cleanup, even on failure:

        async with workspace_manager.prepare(job) as workspace:
            ...  # workspace.path is a fresh, isolated checkout

    Repository source code is treated as untrusted input from this point
    on — see git_client.py for the input validation and injection-safe
    subprocess execution this relies on.

    What this does NOT attempt: hard CPU/memory/network isolation. A clone
    timeout (git_client.py) is the one resource control a bare Python
    process can genuinely enforce; real sandboxing is a containerization
    concern (Phase 12), not something to fake here with process-level
    resource limits that would be a false sense of security on a shared
    host.
    """

    def __init__(self, base_dir: Path | None = None, credentials: RepositoryCredentials | None = None):
        self._base_dir = base_dir
        # None: every clone is anonymous (public repositories only).
        self._credentials = credentials

    async def git_env_for(self, provider: str, repository: str) -> dict[str, str]:
        """Git environment carrying this repository's clone token, or {} when there isn't one."""
        if self._credentials is None:
            return {}
        token = await self._credentials.token_for(provider, repository)
        return git_auth_env(provider, token) if token else {}

    @asynccontextmanager
    async def prepare(self, job: AnalysisJob):
        # tempfile.mkdtemp creates the directory with 0o700 permissions by
        # default — only this process's user can read it, which matters
        # on a shared host running multiple workers/services.
        workspace_dir = Path(
            tempfile.mkdtemp(prefix=f"analysis-{job.job_id}-", dir=self._base_dir)
        )

        try:
            git_env = await self.git_env_for(job.provider, job.repository)

            if job.is_fork:
                # The branch lives in the contributor's fork, not in the
                # repository being merged into — fetching it from the base
                # fails with "couldn't find remote ref", which is how every
                # fork contribution used to die before it was analysed.
                #
                # Cloned anonymously on purpose: the stored token belongs
                # to whoever connected the *base* repository and grants no
                # access to somebody else's fork. Sending it there would
                # hand their credential to a repository they do not own.
                if job.head is not None and job.head.is_private:
                    raise WorkspaceSecurityError(
                        f"{job.head.full_name} is a private fork; its code cannot be fetched "
                        f"with the base repository's credentials."
                    )

                logger.info(
                    "[job:%s] fork pull request: fetching %s from %s",
                    job.job_id, job.branch, job.head.full_name if job.head else "?",
                )
                await clone_commit(
                    job.source_clone_url, job.commit_sha, job.branch, workspace_dir, git_env={}
                )
            else:
                await clone_commit(
                    job.clone_url, job.commit_sha, job.branch, workspace_dir, git_env=git_env
                )

            yield Workspace(job_id=job.job_id, path=workspace_dir, git_env=git_env)

        finally:
            # rmtree is blocking; running it off the event loop keeps
            # cleanup from stalling other async work (health checks,
            # other in-flight operations) on this worker.
            await asyncio.to_thread(shutil.rmtree, workspace_dir, ignore_errors=True)
