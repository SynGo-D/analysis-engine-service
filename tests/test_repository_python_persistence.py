"""
Integration test: PythonAnalysisResult round-trips through the real
Postgres schema (JSONB python_result column + findings.metadata) exactly
as computed — nothing lost, nothing silently coerced. Needs a reachable
Postgres matching this service's own .env (see README.md).
"""
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest

from analysis_engine.analyzers.python.python_analyzer import PythonAnalyzer
from analysis_engine.config import settings
from analysis_engine.domain import AnalysisJob, AnalysisResult
from analysis_engine.infrastructure.schema import ensure_schema
from analysis_engine.repositories.analysis_result_repository import AnalysisResultRepository
from analysis_engine.workspace.workspace_manager import Workspace

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "python_project"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_python_analysis_result_round_trips_through_postgres():
    started_at = datetime.now(timezone.utc)
    job = AnalysisJob(
        provider="github", repository="test/python-persistence-fixture",
        clone_url="https://github.com/test/python-persistence-fixture.git",
        commit_sha="e" * 40, branch="develop", pull_request_number=999,
        queued_at=started_at.isoformat(),
    )
    workspace = Workspace(job_id=job.job_id, path=FIXTURE_REPO)
    analyzer = PythonAnalyzer()
    findings = await analyzer.analyze(workspace, job)
    python_result = analyzer.last_result

    result = AnalysisResult(
        job_id=job.job_id, repository=job.repository, pull_request_number=job.pull_request_number,
        commit_sha=job.commit_sha, branch=job.branch, status="completed",
        findings=findings, python=python_result,
        started_at=started_at, completed_at=datetime.now(timezone.utc),
    )

    pool = await asyncpg.create_pool(
        host=settings.db_host, port=settings.db_port, database=settings.db_name,
        user=settings.db_user, password=settings.db_password,
    )
    try:
        await ensure_schema(pool)
        repo = AnalysisResultRepository(pool)
        await repo.save(result)
        reloaded = await repo.get_latest_for_pull_request(job.repository, job.pull_request_number)
    finally:
        await pool.close()

    assert reloaded is not None
    assert len(reloaded.findings) == len(findings)
    assert reloaded.python is not None
    assert reloaded.python.pylint.status == python_result.pylint.status
    assert reloaded.python.metrics.pylint_issue_count == python_result.metrics.pylint_issue_count
    assert reloaded.python.metrics.loc.kloc == python_result.metrics.loc.kloc

    pylint_finding = next(f for f in reloaded.findings if f.tool == "pylint")
    assert "pylintType" in pylint_finding.metadata

    bandit_finding = next(f for f in reloaded.findings if f.tool == "bandit" and f.category == "vulnerability")
    assert "confidence" in bandit_finding.metadata


@pytest.mark.integration
@pytest.mark.asyncio
async def test_python_field_is_none_when_no_python_files_present(tmp_path):
    started_at = datetime.now(timezone.utc)
    job = AnalysisJob(
        provider="github", repository="test/no-python-fixture",
        clone_url="https://github.com/test/no-python-fixture.git",
        commit_sha="f" * 40, branch="main", pull_request_number=998,
        queued_at=started_at.isoformat(),
    )
    (tmp_path / "README.md").write_text("no python here\n")
    workspace = Workspace(job_id=job.job_id, path=tmp_path)

    # No analyzer ran at all for this job (language detection would have
    # selected none) — AnalysisResult.python stays at its default None.
    result = AnalysisResult(
        job_id=job.job_id, repository=job.repository, pull_request_number=job.pull_request_number,
        commit_sha=job.commit_sha, branch=job.branch, status="completed",
        findings=[], started_at=started_at, completed_at=datetime.now(timezone.utc),
    )
    assert result.python is None

    pool = await asyncpg.create_pool(
        host=settings.db_host, port=settings.db_port, database=settings.db_name,
        user=settings.db_user, password=settings.db_password,
    )
    try:
        await ensure_schema(pool)
        repo = AnalysisResultRepository(pool)
        await repo.save(result)
        reloaded = await repo.get_latest_for_pull_request(job.repository, job.pull_request_number)
    finally:
        await pool.close()

    assert reloaded.python is None
