# RabbitMQ topology constants shared between consumers/ (which declares
# and consumes PR_QUEUE_NAME) and messaging/ (which will declare/publish
# completion events in Phase 10). Centralized here rather than duplicated,
# mirroring webhook-listener's messaging/topology.ts.

# Matches webhook-listener's messaging/topology.ts PR_QUEUE_NAME and
# SynGo-D/RabbitMQ's QUEUES.PR_QUEUE exactly — this is the queue
# webhook-listener publishes PRJob-shaped messages to.
PR_QUEUE_NAME = "pr_queue"
