# api

FastAPI HTTP routes.

- `health.py` — `/health` (liveness) and `/ready` (readiness: DB +
  RabbitMQ reachable).
- `analysis.py` — read-only routes over persisted `AnalysisResult`s
  (`GET /api/repositories/{owner}/{repo}/analysis`,
  `GET /api/repositories/{owner}/{repo}/analysis/pull-requests/{number}`),
  consumed by `main-backend`'s gateway. Analysis jobs themselves are
  consumed asynchronously from RabbitMQ (`consumers/`), never submitted
  synchronously over HTTP.
