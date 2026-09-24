# RabbitMQ topology constants shared between consumers/ (which declares
# and consumes PR_QUEUE_NAME) and messaging/ (which will declare/publish
# completion events in Phase 10). Centralized here rather than duplicated,
# mirroring webhook-listener's messaging/topology.ts.

# Matches webhook-listener's messaging/topology.ts PR_QUEUE_NAME and
# SynGo-D/RabbitMQ's QUEUES.PR_QUEUE exactly — this is the queue
# webhook-listener publishes PRJob-shaped messages to.
PR_QUEUE_NAME = "pr_queue"

DEAD_EXCHANGE_NAME = "webhook.events.dead"
DEAD_QUEUE_NAME = "pr_queue.dead"

# Arguments this service declares PR_QUEUE_NAME with.
#
# MUST be identical to webhook-listener's PR_QUEUE_ARGUMENTS
# (src/messaging/topology.ts). Queue arguments are immutable in RabbitMQ,
# and both services declare this queue — so a mismatch is not a
# disagreement that settles itself. Whichever declares second gets
# PRECONDITION_FAILED and crash-loops on startup. A test in each repo
# pins the literal value so the two cannot drift apart quietly.
#
# The dead-letter exchange is what turns the consumer's
# nack(requeue=False) from "delete this job" into "park it": without it,
# a pull request whose review failed disappeared leaving only a log line.
PR_QUEUE_ARGUMENTS: dict[str, str] = {
    "x-dead-letter-exchange": DEAD_EXCHANGE_NAME,
}
