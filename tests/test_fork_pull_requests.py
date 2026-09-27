"""
Pull requests opened from a fork.

Every one of these failed before: the branch lives in the contributor's
repository, the workspace fetched it from the repository being merged
into, and git answered "couldn't find remote ref" — so the job died
before any analysis ran. For an open-source-shaped project that is most
incoming contributions.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from analysis_engine.domain import AnalysisJob
from analysis_engine.workspace.git_client import WorkspaceSecurityError
from analysis_engine.workspace.workspace_manager import WorkspaceManager

BASE = {
    "provider": "github",
    "repository": "acme/shop",
    "cloneUrl": "https://github.com/acme/shop.git",
    "commit": "a" * 40,
    "branch": "add-feature",
    "prNumber": 5,
    "timestamp": "2026-01-01T00:00:00Z",
}


def _job(**overrides) -> AnalysisJob:
    return AnalysisJob.model_validate({**BASE, **overrides})


def _fork(is_private: bool = False) -> dict:
    return {
        "head": {
            "fullName": "contributor/shop",
            "cloneUrl": "https://github.com/contributor/shop.git",
            "isPrivate": is_private,
        }
    }


class TestJobModel:
    def test_same_repository_pull_request_clones_the_base(self):
        job = _job()
        assert job.is_fork is False
        assert job.source_clone_url == "https://github.com/acme/shop.git"

    def test_fork_pull_request_clones_the_fork(self):
        job = _job(**_fork())
        assert job.is_fork is True
        assert job.source_clone_url == "https://github.com/contributor/shop.git"

    def test_a_job_queued_before_forks_were_understood_still_parses(self):
        assert _job().head is None


class TestWorkspace:
    @pytest.mark.asyncio
    async def test_fetches_the_branch_from_the_fork(self, tmp_path: Path):
        manager = WorkspaceManager(base_dir=tmp_path)

        with patch("analysis_engine.workspace.workspace_manager.clone_commit",
                   new=AsyncMock()) as clone:
            async with manager.prepare(_job(**_fork())):
                pass

        url = clone.await_args.args[0]
        assert url == "https://github.com/contributor/shop.git"

    @pytest.mark.asyncio
    async def test_never_sends_the_base_repository_token_to_a_fork(self, tmp_path: Path):
        """
        The stored token belongs to whoever connected the base repository
        and grants nothing on someone else's fork. Sending it there would
        hand their credential to a repository they do not own.
        """
        credentials = AsyncMock()
        credentials.token_for = AsyncMock(return_value="ghs_secret")
        manager = WorkspaceManager(base_dir=tmp_path, credentials=credentials)

        with patch("analysis_engine.workspace.workspace_manager.clone_commit",
                   new=AsyncMock()) as clone:
            async with manager.prepare(_job(**_fork())):
                pass

        assert clone.await_args.kwargs["git_env"] == {}

    @pytest.mark.asyncio
    async def test_still_authenticates_for_a_same_repository_pull_request(self, tmp_path: Path):
        """Private base repositories must keep working exactly as before."""
        credentials = AsyncMock()
        credentials.token_for = AsyncMock(return_value="ghs_secret")
        manager = WorkspaceManager(base_dir=tmp_path, credentials=credentials)

        with patch("analysis_engine.workspace.workspace_manager.clone_commit",
                   new=AsyncMock()) as clone:
            async with manager.prepare(_job()):
                pass

        assert clone.await_args.args[0] == "https://github.com/acme/shop.git"
        assert clone.await_args.kwargs["git_env"] != {}

    @pytest.mark.asyncio
    async def test_a_private_fork_fails_with_a_reason_rather_than_a_git_error(self, tmp_path: Path):
        manager = WorkspaceManager(base_dir=tmp_path)

        with pytest.raises(WorkspaceSecurityError, match="private fork"):
            async with manager.prepare(_job(**_fork(is_private=True))):
                pass
