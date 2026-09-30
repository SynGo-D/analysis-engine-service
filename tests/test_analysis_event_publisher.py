"""
Publishing analysis.completed.

The behaviour that matters most here is the failure behaviour: a broker
problem must not turn a finished, persisted analysis into a failed job. So
there is a test for the happy path and several for things going wrong.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from analysis_engine.messaging.publisher import AnalysisEventPublisher
from analysis_engine.messaging.topology import (
    ANALYSIS_COMPLETED_PREFIX,
    DEBT_QUEUE_ARGUMENTS,
    DEBT_ROUTING_PATTERN,
)


def _job(provider: str = "github") -> MagicMock:
    job = MagicMock()
    job.job_id = "job-1"
    job.provider = provider
    job.repository = "owner/repo"
    job.pull_request_number = 7
    job.commit_sha = "a" * 40
    job.branch = "feature/x"
    return job


def _result(status: str = "completed", findings: int = 3) -> MagicMock:
    result = MagicMock()
    result.result_id = "result-1"
    result.status = status
    result.findings = [MagicMock() for _ in range(findings)]
    return result


class TestPublishing:
    @pytest.mark.asyncio
    async def test_routing_key_carries_the_provider(self):
        exchange = MagicMock()
        exchange.publish = AsyncMock()

        assert await AnalysisEventPublisher(exchange).publish_completed(_job("gitlab"), _result())

        assert exchange.publish.await_args.kwargs["routing_key"] == (
            f"{ANALYSIS_COMPLETED_PREFIX}.gitlab"
        )

    @pytest.mark.asyncio
    async def test_body_identifies_the_pull_request_without_carrying_findings(self):
        exchange = MagicMock()
        exchange.publish = AsyncMock()

        await AnalysisEventPublisher(exchange).publish_completed(_job(), _result(findings=3))

        body = json.loads(exchange.publish.await_args.args[0].body)
        assert body["repository"] == "owner/repo"
        assert body["pullRequestNumber"] == 7
        assert body["commitSha"] == "a" * 40
        assert body["findingsCount"] == 3
        # The findings themselves stay in the database — a consumer reads
        # the current ones rather than a snapshot in a message.
        assert "findings" not in body

    @pytest.mark.asyncio
    async def test_message_is_persistent(self):
        exchange = MagicMock()
        exchange.publish = AsyncMock()

        await AnalysisEventPublisher(exchange).publish_completed(_job(), _result())

        # A transient message on a durable exchange bound to a durable
        # queue would be lost on a broker restart for no reason.
        assert exchange.publish.await_args.args[0].delivery_mode == 2


class TestFailureIsNeverFatal:
    """The analysis is already saved. Nothing here may raise."""

    @pytest.mark.asyncio
    async def test_a_broker_error_is_swallowed_and_reported_as_false(self):
        exchange = MagicMock()
        exchange.publish = AsyncMock(side_effect=ConnectionError("broker gone"))

        published = await AnalysisEventPublisher(exchange).publish_completed(_job(), _result())

        assert published is False

    @pytest.mark.asyncio
    async def test_an_unserialisable_payload_is_swallowed_too(self):
        exchange = MagicMock()
        exchange.publish = AsyncMock()
        job = _job()
        job.pull_request_number = {1, 2}          # a set is not JSON

        assert await AnalysisEventPublisher(exchange).publish_completed(job, _result()) is False
        exchange.publish.assert_not_awaited()


class TestContractWithTheDebtService:
    def test_pins_the_binding_and_arguments_the_other_service_must_match(self):
        # technical-debt-service declares debt_queue and binds it to this
        # pattern. Queue arguments are immutable, so if this changes
        # without the matching change in its src/messaging/topology.py,
        # whichever declares second crash-loops on PRECONDITION_FAILED.
        assert DEBT_ROUTING_PATTERN == "analysis.completed.#"
        # Its own dead-letter exchange: a parked analysis.completed event
        # keeps its routing key, which webhook.events.dead's `pr.#`
        # binding does not match, so sending it there would discard it.
        assert DEBT_QUEUE_ARGUMENTS == {"x-dead-letter-exchange": "analysis.events.dead"}
