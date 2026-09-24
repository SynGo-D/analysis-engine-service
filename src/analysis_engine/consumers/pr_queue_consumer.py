import json
import logging

from aio_pika import IncomingMessage
from aio_pika.abc import AbstractRobustChannel
from pydantic import ValidationError

from ..application.orchestrator import AnalysisOrchestrator
from ..config import settings
from ..domain import AnalysisJob, AnalysisResult
from ..messaging.topology import PR_QUEUE_ARGUMENTS, PR_QUEUE_NAME
from ..repositories.agent_review_repository import AgentReviewRepository
from ..repositories.analysis_result_repository import AnalysisResultRepository

logger = logging.getLogger(__name__)


class PRQueueConsumer:
    """
    Consumes AnalysisJob messages from pr_queue.

    Deliberately thin, mirroring webhook-listener's controller layer:
    parse and validate the message, delegate to the orchestrator for
    everything else, ack/nack based on the outcome. No workspace/analyzer/
    persistence logic belongs here.
    """

    def __init__(
        self,
        orchestrator: AnalysisOrchestrator,
        repository: AnalysisResultRepository,
        review_repository: AgentReviewRepository | None = None,
    ):
        self._orchestrator = orchestrator
        self._repository = repository
        self._review_repository = review_repository

    async def start(self, channel: AbstractRobustChannel) -> None:
        # Bounds how many unacked jobs this worker holds at once — see
        # config.py's consumer_prefetch_count docstring. Without this,
        # aio-pika's default is unbounded, which is wrong for long-running
        # work like this service's jobs.
        await channel.set_qos(prefetch_count=settings.consumer_prefetch_count)

        # Idempotent — safe regardless of whether webhook-listener's or
        # this service's declaration reaches the broker first, same
        # reasoning as webhook-listener's topology.ts, *as long as both
        # pass the same arguments*. They are shared deliberately: queue
        # arguments are immutable, so a mismatch means whichever service
        # declares second fails with PRECONDITION_FAILED and crash-loops.
        queue = await channel.declare_queue(
            PR_QUEUE_NAME, durable=True, arguments=dict(PR_QUEUE_ARGUMENTS)
        )

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
            # The linter result is saved the moment it exists, before the
            # AI review starts; the review is saved as "running" and then
            # with its outcome. A review failure never loses the result.
            result = await self._orchestrator.run(
                job,
                on_result=self._repository.save,
                on_review=self._review_repository.save if self._review_repository else None,
            )
            # After run() returns, because the timings include the AI
            # review and the total, neither of which exists when the
            # result is first saved.
            await self._repository.update_timings(result.result_id, result.timings)

            await message.ack()
            self._log_result(job, result)

        except Exception as error:
            # requeue=False, but this no longer destroys the job: pr_queue
            # is declared with a dead-letter exchange, so the message is
            # parked on pr_queue.dead with an x-death header recording why
            # and when. It can be inspected and replayed.
            #
            # Still not requeued, deliberately — the same job failing the
            # same way in a loop is worse than one parked message, and
            # telling a transient failure from a permanent one needs the
            # retry-count tracking that does not exist yet.
            logger.exception(
                "[job:%s] processing failed, dead-lettering to %s: %s",
                job.job_id, "pr_queue.dead", error,
            )
            await message.nack(requeue=False)

    def _log_result(self, job: AnalysisJob, result: AnalysisResult) -> None:
        """
        Prints a readable findings summary to the log in addition to the
        Phase 9 persistence above — useful for following a job live without
        querying the database, and still the only signal until Phase 10
        (publishing an analysis.completed event) exists.
        """
        logger.info(
            "[job:%s] %s — %d finding(s) in %s PR#%d (branch %s @ %s)",
            job.job_id, result.status, len(result.findings),
            job.repository, job.pull_request_number, job.branch, job.commit_sha[:12],
        )
        for finding in result.findings:
            location = f"{finding.file_path}:{finding.line}" if finding.line else finding.file_path
            logger.info(
                "[job:%s]   [%s/%s] %s — %s — %s",
                job.job_id, finding.tool, finding.severity, location, finding.rule_id, finding.message,
            )
