# messaging

RabbitMQ publisher — the only layer (alongside `consumers/`) that knows
about RabbitMQ. Publishes `analysis.completed` / `analysis.failed` events
for downstream services once an analysis job finishes.

Connection lifecycle itself lives in `infrastructure/rabbitmq.py`, shared
with `consumers/`.

Planned: Phase 10, alongside idempotent job tracking and retry/DLQ
handling.
