# Analysis Engine

Analysis microservice for the CodePulse automated code-review and
technical-debt platform. Consumes normalized PR/MR analysis jobs from
RabbitMQ, runs static-analysis tools through Reviewdog, normalizes and
persists findings, and publishes completion events.

**Reviewdog is the review/diagnostic aggregation and reporting layer; this
service owns orchestration and result processing.** This keeps the
architecture from becoming tightly coupled to Reviewdog and makes it
possible to replace or add analysis tools later.

> **Interop note:** this service consumes from `pr_queue` — the same
> queue `webhook-listener` publishes `PRJob`-shaped messages to (see that
> repo's `src/messaging/`). It must connect to the **same shared RabbitMQ
> broker**, not stand up its own — see `docker-compose.yml`.

## Architecture

```text
                    RabbitMQ
                       │
                       │ Analysis Job
                       ▼
              ┌──────────────────┐
              │ Analysis Engine  │
              │     FastAPI      │
              └────────┬─────────┘
                       │
                       ▼
              Analysis Orchestrator
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
     Language       Analyzer      Workspace
     Detection      Factory       Manager
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
           ESLint    Pylint    Cppcheck
             │         │         │
             └─────────┼─────────┘
                       ▼
                   Reviewdog
                       │
                       ▼
              Finding Normalizer
                       │
                       ▼
             Quality/Debt Results
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
      PostgreSQL                 MongoDB
          │
          ▼
     RabbitMQ
          │
          ▼
   analysis.completed
```

### Layers

| Layer | Responsibility |
|---|---|
| `api/` | FastAPI health/readiness routes (jobs arrive via RabbitMQ, not HTTP) |
| `consumers/` | Entry point for analysis jobs — the RabbitMQ equivalent of a controller |
| `application/` | Orchestrates the full pipeline (Pipeline/Command Pattern) |
| `domain/` | Internal models: `AnalysisJob`, `Finding`, `AnalysisResult` |
| `analyzers/` | Tool adapters (ESLint, Pylint, Radon, Cppcheck) — Adapter Pattern |
| `factories/` | Selects the right analyzer(s) for detected languages — Factory Pattern |
| `workspace/` | Isolated per-job temp workspace, secure clone/checkout, cleanup |
| `repositories/` | Postgres (and Mongo, if justified) persistence — Repository Pattern |
| `messaging/` | Publishes `analysis.completed`/`analysis.failed` |
| `infrastructure/`, `config.py` | Cross-cutting infrastructure (DB/RabbitMQ connections, settings) |

Each layer's `README.md` describes its responsibility and which phase adds
real code to it.

## Local development

Requires Docker (for this service's own Postgres) and the **same shared
RabbitMQ broker `webhook-listener`'s `docker-compose.yml` provides** —
start that one first.

```bash
# 1. Start this service's own Postgres (its own DB, port 5434)
docker compose up -d

# 2. Create and activate a virtualenv, install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Copy env config (already matches docker-compose.yml's ports/credentials,
#    and assumes webhook-listener's shared RabbitMQ is already running)
cp .env.example .env

# 4. Start the service
uvicorn analysis_engine.main:app --reload --app-dir src --port 8000
```

```bash
curl http://localhost:8000/health   # liveness — process is up
curl http://localhost:8000/ready    # readiness — DB + RabbitMQ reachable
```

## Build roadmap

Built incrementally, one phase at a time:

1. ✅ Project structure + infrastructure
2. ✅ Domain models (`AnalysisJob`, `Finding`, `AnalysisResult`)
3. ✅ RabbitMQ consumer (`pr_queue`, correlation IDs, job validation)
4. ✅ Workspace manager (isolated temp workspace, secure clone/checkout)
5. ✅ Language detection + analyzer abstraction (Strategy) + Factory Pattern
6. Analyzer adapters (ESLint, Pylint, Radon, Cppcheck) + Reviewdog integration
7. Finding normalization + fingerprint/deduplication
8. Quality/technical-debt metrics calculation (SQALE-oriented)
9. Persistence (Postgres, Mongo if justified)
10. Publish completion/failure events + idempotent job tracking + retry/DLQ
11. Testing (unit, integration, security, e2e)
12. Docker/Kubernetes/AWS deployment configuration
