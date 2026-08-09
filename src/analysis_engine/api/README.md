# api

FastAPI HTTP routes. Health/readiness only (`health.py`) — per the spec,
analysis jobs are consumed asynchronously from RabbitMQ (`consumers/`),
not submitted synchronously over HTTP. Any future operational endpoints
(e.g. manual re-run, job status lookup) belong here.
