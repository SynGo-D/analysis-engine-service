"""
How the consumer declares its queue, and what it does with a job it
cannot process.

Both matter for the same reason: a nack with requeue=False deletes the
message unless the queue carries a dead-letter exchange. The declaration
and the nack are two halves of one behaviour, so they are tested
together.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from analysis_engine.consumers.pr_queue_consumer import PRQueueConsumer
from analysis_engine.messaging.topology import PR_QUEUE_ARGUMENTS, PR_QUEUE_NAME


def _message(body: dict | str) -> MagicMock:
    message = MagicMock()
    message.body = (json.dumps(body) if isinstance(body, dict) else body).encode()
    message.ack = AsyncMock()
    message.nack = AsyncMock()
    return message


def _valid_job() -> dict:
    return {
        "provider": "github", "repository": "owner/repo",
        "cloneUrl": "https://example.invalid/r.git", "commit": "a" * 40,
        "branch": "main", "prNumber": 1, "timestamp": "2026-01-01T00:00:00Z",
    }


class TestQueueDeclaration:
    @pytest.mark.asyncio
    async def test_declares_the_queue_with_a_dead_letter_exchange(self):
        channel = MagicMock()
        channel.set_qos = AsyncMock()
        queue = MagicMock()
        queue.consume = AsyncMock()
        channel.declare_queue = AsyncMock(return_value=queue)

        await PRQueueConsumer(orchestrator=MagicMock(), repository=MagicMock()).start(channel)

        channel.declare_queue.assert_awaited_once()
        kwargs = channel.declare_queue.await_args.kwargs
        assert channel.declare_queue.await_args.args[0] == PR_QUEUE_NAME
        assert kwargs["durable"] is True
        assert kwargs["arguments"] == {"x-dead-letter-exchange": "webhook.events.dead"}

    def test_pins_the_exact_arguments_webhook_listener_must_also_use(self):
        # Queue arguments are immutable and both services declare this
        # queue, so changing this without the matching change in
        # webhook-listener's messaging/topology.ts makes whichever
        # declares second crash-loop on PRECONDITION_FAILED. If this
        # fails, change both or neither.
        assert PR_QUEUE_ARGUMENTS == {"x-dead-letter-exchange": "webhook.events.dead"}


class TestFailureHandling:
    """Each of these now parks the message rather than destroying it."""

    @pytest.mark.asyncio
    async def test_unparsable_body_is_nacked_without_requeue(self):
        message = _message("not json at all")

        await PRQueueConsumer(orchestrator=MagicMock(), repository=MagicMock())._handle_message(message)

        message.nack.assert_awaited_once_with(requeue=False)
        message.ack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_body_that_is_not_a_job_is_nacked_without_requeue(self):
        message = _message({"provider": "github"})   # missing everything else

        await PRQueueConsumer(orchestrator=MagicMock(), repository=MagicMock())._handle_message(message)

        message.nack.assert_awaited_once_with(requeue=False)

    @pytest.mark.asyncio
    async def test_a_failing_analysis_is_nacked_without_requeue(self):
        orchestrator = MagicMock()
        orchestrator.run = AsyncMock(side_effect=RuntimeError("clone exploded"))
        message = _message(_valid_job())

        await PRQueueConsumer(orchestrator=orchestrator, repository=MagicMock())._handle_message(message)

        # requeue=False on purpose: the same job failing the same way in a
        # loop is worse than one parked message. The dead-letter exchange
        # is what makes that safe.
        message.nack.assert_awaited_once_with(requeue=False)
        message.ack.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_successful_job_is_acked_and_never_dead_lettered(self):
        result = MagicMock(result_id="r1", timings={"total": 10}, status="completed", findings=[])
        orchestrator = MagicMock()
        orchestrator.run = AsyncMock(return_value=result)
        repository = MagicMock()
        repository.update_timings = AsyncMock()
        message = _message(_valid_job())

        await PRQueueConsumer(orchestrator=orchestrator, repository=repository)._handle_message(message)

        message.ack.assert_awaited_once()
        message.nack.assert_not_awaited()
