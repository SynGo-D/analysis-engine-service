from datetime import datetime, timezone, timedelta

import pytest

from analysis_engine.application.orchestrator import AnalysisOrchestrator
from analysis_engine.application.phase_timer import PhaseTimer
from analysis_engine.domain import AnalysisJob


class TestPhaseTimer:
    def test_records_a_phase_in_milliseconds(self):
        timer = PhaseTimer()

        with timer.phase("clone"):
            pass

        assert "clone" in timer.as_dict()
        assert timer.as_dict()["clone"] >= 0

    def test_a_phase_that_raises_still_records_how_long_it_took(self):
        """How long something ran before failing is usually the point."""
        timer = PhaseTimer()

        with pytest.raises(ValueError):
            with timer.phase("analyze"):
                raise ValueError("tool exploded")

        assert "analyze" in timer.as_dict()

    def test_repeated_names_accumulate(self):
        """A stage that runs per analyzer should sum, not overwrite."""
        timer = PhaseTimer()
        timer.record("analyzer", 100)
        timer.record("analyzer", 250)

        assert timer.as_dict()["analyzer"] == 350

    def test_a_negative_duration_is_never_recorded(self):
        timer = PhaseTimer()
        timer.record("queue_wait", -5000)

        assert timer.as_dict()["queue_wait"] == 0

    def test_snapshot_does_not_alias_the_timer(self):
        timer = PhaseTimer()
        timer.record("clone", 10)
        snapshot = timer.as_dict()
        timer.record("clone", 10)

        assert snapshot["clone"] == 10


def _job(queued_at: str, received_at: datetime) -> AnalysisJob:
    job = AnalysisJob(
        provider="github", repository="owner/repo", cloneUrl="https://example.invalid/r.git",
        commit="a" * 40, branch="main", prNumber=1, timestamp=queued_at,
    )
    # received_at defaults to "now"; a queue wait needs both ends fixed.
    return job.model_copy(update={"received_at": received_at})


class TestQueueWait:
    def test_measures_the_gap_between_queueing_and_pickup(self):
        received = datetime(2026, 9, 24, 12, 0, 30, tzinfo=timezone.utc)
        job = _job("2026-09-24T12:00:00Z", received)

        assert AnalysisOrchestrator._queue_wait_ms(job) == 30_000

    def test_two_clocks_disagreeing_reports_zero_not_a_negative_wait(self):
        """The timestamps come from two services; one can run ahead."""
        received = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)
        job = _job("2026-09-24T12:00:30Z", received)

        assert AnalysisOrchestrator._queue_wait_ms(job) == 0

    def test_an_unparsable_timestamp_reports_zero_rather_than_guessing(self):
        job = _job("not a timestamp", datetime.now(timezone.utc))

        assert AnalysisOrchestrator._queue_wait_ms(job) == 0

    def test_a_timestamp_without_a_zone_is_read_as_utc(self):
        received = datetime(2026, 9, 24, 12, 0, 10, tzinfo=timezone.utc)
        job = _job("2026-09-24T12:00:00", received)

        assert AnalysisOrchestrator._queue_wait_ms(job) == 10_000
