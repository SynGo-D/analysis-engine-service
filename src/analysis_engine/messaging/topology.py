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


# ---------------------------------------------------------------------------
# Completion events (what this service publishes, rather than consumes)
# ---------------------------------------------------------------------------
# A second topic exchange, in front of whatever wants to know that an
# analysis finished. It is deliberately NOT webhook.events: that exchange
# carries "a pull request changed, someone should analyse it", and this one
# carries "an analysis exists, its findings can be read". Putting both on
# one exchange would mean every consumer had to filter by routing key to
# avoid reacting to the wrong half of the cycle.
#
# Publishing is fire-and-forget by design. A completion event that cannot
# be published must never fail the analysis that just succeeded — the
# result is already in the database, and the worst case is that downstream
# debt is calculated later, by hand, from the same data.
ANALYSIS_EXCHANGE_NAME = "analysis.events"

# analysis.completed.<provider>, e.g. analysis.completed.github. The
# provider is in the key rather than only the body so a future consumer can
# bind to one provider without decoding every message.
ANALYSIS_COMPLETED_PREFIX = "analysis.completed"

# Bound and consumed by technical-debt-service, declared there. Named here
# because the exchange and the binding pattern are a contract between the
# two services, and a contract kept in one repository is one nobody in the
# other can see.
DEBT_QUEUE_NAME = "debt_queue"
DEBT_ROUTING_PATTERN = "analysis.completed.#"

# Its own dead-letter exchange, NOT webhook.events.dead.
#
# Reusing that one looks tempting and is silently broken: pr_queue.dead is
# bound to it under `pr.#`, and a dead-lettered debt message keeps its
# original routing key of `analysis.completed.<provider>`. That matches no
# binding, so the message would be dropped by the very mechanism meant to
# preserve it — the same failure the dead-letter work was done to stop,
# reintroduced one exchange along.
DEAD_ANALYSIS_EXCHANGE_NAME = "analysis.events.dead"
DEAD_DEBT_QUEUE_NAME = "debt_queue.dead"

# Bound with `#` rather than a narrower pattern: this is a sink, and a
# parked message that cannot be routed is indistinguishable from one that
# was never parked at all.
DEAD_DEBT_ROUTING_PATTERN = "#"

# Same reasoning as PR_QUEUE_ARGUMENTS: a nack with requeue=False deletes
# the message unless the queue carries a dead-letter exchange. Debt
# calculation costs real money per finding, so a failure worth retrying is
# worth being able to find.
#
# MUST be identical to technical-debt-service's DEBT_QUEUE_ARGUMENTS
# (src/messaging/topology.py). Queue arguments are immutable in RabbitMQ.
DEBT_QUEUE_ARGUMENTS: dict[str, str] = {
    "x-dead-letter-exchange": DEAD_ANALYSIS_EXCHANGE_NAME,
}
