"""
The contributor aggregation, against a real Postgres.

These are integration tests on purpose: the whole feature is two SQL
statements, and the things most likely to be wrong about them — counting
a re-analysed pull request twice, summing JSONB fields that are text, a
join that multiplies rows — are exactly what an in-memory fake would not
reproduce.
"""
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from analysis_engine.config import settings
from analysis_engine.domain import AnalysisMetrics, AnalysisResult, PullRequestChanges
from analysis_engine.domain.change_set import ChangeSet
from analysis_engine.infrastructure.schema import ensure_schema
from analysis_engine.repositories.analysis_result_repository import AnalysisResultRepository

REPOSITORY = f"test/contributors-{uuid4().hex[:8]}"


def _result(author: str | None, pr: int, *, issues: int, errors: int,
            added: int, removed: int, files: int, minutes_ago: int = 0) -> AnalysisResult:
    now = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return AnalysisResult(
        job_id=uuid4(),
        repository=REPOSITORY,
        pull_request_number=pr,
        commit_sha=uuid4().hex[:40],
        branch=f"feature/{pr}",
        author_username=author,
        author_provider_id="77" if author else None,
        status="completed",
        findings=[],
        metrics=AnalysisMetrics(
            files_analyzed=files, loc=100, errors=errors,
            warnings=issues - errors, total_issues=issues,
        ),
        changes=PullRequestChanges(
            status="available",
            change_set=ChangeSet(
                base_sha="b" * 40, head_sha="h" * 40, target_branch="main",
                files=[], excluded_files=[],
            ),
            changed_symbols=[],
            files_changed=files, lines_added=added, lines_removed=removed,
            findings_on_changed_lines=0,
        ),
        started_at=now,
        completed_at=now,
    )


@pytest_asyncio.fixture
async def repository():
    pool = await asyncpg.create_pool(
        host=settings.db_host, port=settings.db_port, database=settings.db_name,
        user=settings.db_user, password=settings.db_password,
    )
    await ensure_schema(pool)
    yield AnalysisResultRepository(pool)
    await pool.execute("DELETE FROM analysis_results WHERE repository = $1", REPOSITORY)
    await pool.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_groups_work_by_author(repository):
    await repository.save(_result("amara", 1, issues=4, errors=1, added=30, removed=5, files=2))
    await repository.save(_result("amara", 2, issues=2, errors=0, added=10, removed=2, files=1))
    await repository.save(_result("bimal", 3, issues=7, errors=3, added=90, removed=40, files=6))

    rows = {row["username"]: row for row in await repository.contributor_summary(REPOSITORY)}

    assert rows["amara"]["pull_requests"] == 2
    assert rows["amara"]["issues"] == 6
    assert rows["amara"]["errors"] == 1
    assert rows["amara"]["lines_added"] == 40
    assert rows["amara"]["lines_removed"] == 7
    assert rows["bimal"]["pull_requests"] == 1
    assert rows["bimal"]["lines_added"] == 90


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_re_analysed_pull_request_counts_once(repository):
    """
    A pull request is analysed again on every push. Counting rows would
    report someone who pushed four times as having opened four pull
    requests.
    """
    for _ in range(4):
        await repository.save(_result("amara", 10, issues=1, errors=0, added=5, removed=1, files=1))

    row = next(r for r in await repository.contributor_summary(REPOSITORY) if r["username"] == "amara")

    assert row["pull_requests"] == 1
    assert row["analyses"] == 4


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unattributed_analyses_are_left_out(repository):
    """Analyses stored before the author was carried through have none."""
    await repository.save(_result(None, 20, issues=5, errors=2, added=10, removed=0, files=1))
    await repository.save(_result("amara", 21, issues=1, errors=0, added=1, removed=0, files=1))

    usernames = [row["username"] for row in await repository.contributor_summary(REPOSITORY)]

    assert usernames == ["amara"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_busiest_contributor_comes_first(repository):
    await repository.save(_result("quiet", 30, issues=0, errors=0, added=1, removed=0, files=1))
    await repository.save(_result("busy", 31, issues=0, errors=0, added=1, removed=0, files=1))
    await repository.save(_result("busy", 32, issues=0, errors=0, added=1, removed=0, files=1))

    rows = await repository.contributor_summary(REPOSITORY)

    assert [row["username"] for row in rows] == ["busy", "quiet"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_contributors_for_an_unknown_repository(repository):
    assert await repository.contributor_summary("nobody/nothing") == []
    assert await repository.contributor_review_findings("nobody/nothing") == {}
