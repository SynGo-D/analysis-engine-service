# consumers

Entry point for analysis jobs — the RabbitMQ equivalent of an HTTP
controller.

`PRQueueConsumer` consumes from `pr_queue` (declared idempotently here,
same reasoning as webhook-listener's topology.ts — safe regardless of
which service's declaration reaches the broker first), with
`prefetch_count` bounding how many unacked jobs this worker holds at once
(see `config.py` — analysis jobs are long-running, unlike a webhook HTTP
request, so this matters a lot more here). Deliberately thin:

1. Parse the message body as JSON, validate into an `AnalysisJob`.
2. Delegate to `AnalysisOrchestrator` for everything else.
3. Ack on success; nack (no requeue, for now) on any failure —
   distinguishing transient-vs-permanent failures for real retry/DLQ
   policy is Phase 10.

No repository checkout, tool execution, or persistence logic belongs
here — see `application/` for orchestration.
