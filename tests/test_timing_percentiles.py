"""
The percentile query, against a real Postgres.

The risk in it is SQL, not Python: unpacking a JSONB object into rows,
casting its values to numeric, and computing percentiles over them. A
fake would reproduce none of that.
"""
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from analysis_engine.config import settings
from analysis_engine.domain import AnalysisMetrics, AnalysisResult
from analysis_engine.infrastructure.schema import ensure_schema
from analysis_engine.repositories.analysis_result_repository import AnalysisResultRepository

REPOSITORY = f"test/timings-{uuid4().hex[:8]}"


def _result(timings: dict[str, int], days_ago: int = 0) -> AnalysisResult:
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return AnalysisResult(
        job_id=uuid4(), repository=REPOSITORY, pull_request_number=1,
        commit_sha=uuid4().hex[:40], branch="main", status="completed",
        findings=[], metrics=AnalysisMetrics(), timings=timings,
        started_at=when, completed_at=when,
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
async def test_timings_survive_a_round_trip(repository):
    await repository.save(_result({"workspace": 900, "ai_review": 22000, "total": 34000}))

    reloaded = await repository.get_latest_for_pull_request(REPOSITORY, 1)

    assert reloaded.timings == {"workspace": 900, "ai_review": 22000, "total": 34000}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reports_p50_and_p95_per_phase(repository):
    for ms in (100, 200, 300, 400, 10_000):
        await repository.save(_result({"ai_review": ms}))

    phases = {p["phase"]: p for p in (await repository.timing_percentiles(REPOSITORY, 7))["phases"]}

    assert phases["ai_review"]["samples"] == 5
    assert phases["ai_review"]["p50_ms"] == 300
    # p95 has to follow the slow one — that is the number someone waiting
    # actually experiences, and the mean would bury it.
    assert phases["ai_review"]["p95_ms"] > 1_000
    assert phases["ai_review"]["max_ms"] == 10_000


@pytest.mark.integration
@pytest.mark.asyncio
async def test_slowest_phase_comes_first(repository):
    await repository.save(_result({"workspace": 100, "ai_review": 20_000, "count_lines": 5}))

    phases = [p["phase"] for p in (await repository.timing_percentiles(REPOSITORY, 7))["phases"]]

    assert phases[0] == "ai_review"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_new_phase_needs_no_change_here(repository):
    """Stage names are read from the data, not listed in the query."""
    await repository.save(_result({"some_future_stage": 4_200}))

    phases = {p["phase"] for p in (await repository.timing_percentiles(REPOSITORY, 7))["phases"]}

    assert "some_future_stage" in phases


@pytest.mark.integration
@pytest.mark.asyncio
async def test_results_without_timings_are_ignored(repository):
    """Everything stored before this was measured has an empty object."""
    await repository.save(_result({}))

    assert (await repository.timing_percentiles(REPOSITORY, 7))["phases"] == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_older_jobs_fall_outside_the_window(repository):
    await repository.save(_result({"ai_review": 500}, days_ago=30))

    assert (await repository.timing_percentiles(REPOSITORY, 7))["phases"] == []
