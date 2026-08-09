import asyncio
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from ..domain import AnalysisJob
from .git_client import clone_commit


class Workspace:
    """An isolated, per-job filesystem workspace. Never reused across jobs."""

    def __init__(self, job_id: UUID, path: Path):
        self.job_id = job_id
        self.path = path


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

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir

    @asynccontextmanager
    async def prepare(self, job: AnalysisJob):
        # tempfile.mkdtemp creates the directory with 0o700 permissions by
        # default — only this process's user can read it, which matters
        # on a shared host running multiple workers/services.
        workspace_dir = Path(
            tempfile.mkdtemp(prefix=f"analysis-{job.job_id}-", dir=self._base_dir)
        )

        try:
            await clone_commit(job.clone_url, job.commit_sha, job.branch, workspace_dir)
            yield Workspace(job_id=job.job_id, path=workspace_dir)

        finally:
            # rmtree is blocking; running it off the event loop keeps
            # cleanup from stalling other async work (health checks,
            # other in-flight operations) on this worker.
            await asyncio.to_thread(shutil.rmtree, workspace_dir, ignore_errors=True)
