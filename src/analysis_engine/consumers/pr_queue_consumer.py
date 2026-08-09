import json
import logging

from aio_pika import IncomingMessage
from aio_pika.abc import AbstractRobustChannel
from pydantic import ValidationError

from ..application.orchestrator import AnalysisOrchestrator
from ..config import settings
from ..domain import AnalysisJob
from ..messaging.topology import PR_QUEUE_NAME

logger = logging.getLogger(__name__)


class PRQueueConsumer:
    """
    Consumes AnalysisJob messages from pr_queue.

    Deliberately thin, mirroring webhook-listener's controller layer:
    parse and validate the message, delegate to the orchestrator for
    everything else, ack/nack based on the outcome. No workspace/analyzer/
    persistence logic belongs here.
    """

    def __init__(self, orchestrator: AnalysisOrchestrator):
        self._orchestrator = orchestrator

    async def start(self, channel: AbstractRobustChannel) -> None:
        # Bounds how many unacked jobs this worker holds at once — see
        # config.py's consumer_prefetch_count docstring. Without this,
        # aio-pika's default is unbounded, which is wrong for long-running
        # work like this service's jobs.
        await channel.set_qos(prefetch_count=settings.consumer_prefetch_count)

        # Idempotent — safe regardless of whether webhook-listener's or
        # this service's declaration reaches the broker first, same
        # reasoning as webhook-listener's topology.ts.
        queue = await channel.declare_queue(PR_QUEUE_NAME, durable=True)

        await queue.consume(self._handle_message)
        logger.info(
            "Consuming from %s (prefetch=%d)", PR_QUEUE_NAME, settings.consumer_prefetch_count
        )

    async def _handle_message(self, message: IncomingMessage) -> None:
        try:
            payload = json.loads(message.body)
        except json.JSONDecodeError as error:
            logger.error("Malformed message body (not valid JSON): %s", error)
            await message.nack(requeue=False)
            return

        try:
            job = AnalysisJob.model_validate(payload)
        except ValidationError as error:
            logger.error("Message failed AnalysisJob validation: %s", error)
            await message.nack(requeue=False)
            return

        logger.info(
            "[job:%s] received %s PR #%d (%s @ %s)",
            job.job_id, job.provider, job.pull_request_number, job.repository, job.commit_sha,
        )

        try:
            await self._orchestrator.run(job)
            await message.ack()
            logger.info("[job:%s] completed", job.job_id)

        except Exception as error:
            # Phase 10 refines this: distinguishing transient failures
            # (worth requeuing/retrying) from permanent ones (should go to
            # a dead-letter queue) needs real retry-count tracking, which
            # doesn't exist yet. For now: log clearly and drop rather than
            # requeue, to avoid an infinite redelivery loop while the
            # orchestrator is still a stub (Phases 4-10).
            logger.exception("[job:%s] processing failed: %s", job.job_id, error)
            await message.nack(requeue=False)
