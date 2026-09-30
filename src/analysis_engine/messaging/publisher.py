"""
Announcing that an analysis finished.

The result is already in the database by the time this runs, which is what
makes the whole thing safe to treat as best-effort: a consumer that misses
an event can still read the same findings by repository and pull request
number, which is exactly what the manual "Calculate Debt" button does. So
publishing never raises into the caller — an analysis that succeeded must
not be reported as failed because a broker was briefly unreachable.

The payload carries identifiers, not findings. Findings live in the
database, they can be large, and a consumer that reads them from there
gets whatever the latest analysis holds rather than a snapshot that was
accurate when the message was written.
"""
import json
import logging

from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractExchange, AbstractRobustChannel

from ..domain import AnalysisJob, AnalysisResult
from .topology import (
    ANALYSIS_COMPLETED_PREFIX,
    ANALYSIS_EXCHANGE_NAME,
)

logger = logging.getLogger(__name__)


async def declare_analysis_exchange(channel: AbstractRobustChannel) -> AbstractExchange:
    """
    Durable topic exchange, declared by whichever side starts first.

    Durable because a broker restart must not silently drop the exchange
    and leave every later publish going nowhere — an unroutable message is
    at least visible, a missing exchange is a channel error.
    """
    return await channel.declare_exchange(
        ANALYSIS_EXCHANGE_NAME,
        type="topic",
        durable=True,
    )


class AnalysisEventPublisher:
    """Publishes analysis.completed.<provider> to the analysis exchange."""

    def __init__(self, exchange: AbstractExchange):
        self._exchange = exchange

    async def publish_completed(self, job: AnalysisJob, result: AnalysisResult) -> bool:
        """
        Returns whether the event was published. Never raises.

        The boolean is for the caller's logging, not for control flow:
        there is no useful recovery here, and the caller has already
        acknowledged a job whose real work is done.
        """
        routing_key = f"{ANALYSIS_COMPLETED_PREFIX}.{job.provider}"

        payload = {
            "resultId": str(result.result_id),
            "jobId": str(job.job_id),
            "provider": job.provider,
            "repository": job.repository,
            "pullRequestNumber": job.pull_request_number,
            "commitSha": job.commit_sha,
            "branch": job.branch,
            "status": result.status,
            # A consumer can decide whether a run is worth its cost before
            # reading anything: debt classification spends per finding.
            "findingsCount": len(result.findings),
        }

        try:
            await self._exchange.publish(
                Message(
                    body=json.dumps(payload).encode(),
                    content_type="application/json",
                    # Survives a broker restart, matching the durable
                    # exchange and the durable queues bound to it. A
                    # transient message would make all of that pointless.
                    delivery_mode=DeliveryMode.PERSISTENT,
                ),
                routing_key=routing_key,
            )
            logger.info(
                "[job:%s] published %s for %s PR #%d (%d finding(s))",
                job.job_id, routing_key, job.repository,
                job.pull_request_number, len(result.findings),
            )
            return True

        except Exception as error:
            # Deliberately swallowed. See the module docstring: the
            # analysis is already persisted, and the downstream work can
            # still be triggered by hand from the same data.
            logger.warning(
                "[job:%s] could not publish %s (the analysis itself is unaffected): %s",
                job.job_id, routing_key, error,
            )
            return False
