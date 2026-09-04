# Analysis Engine

A dedicated ESLint (JavaScript/TypeScript) code-quality analyzer
microservice for the CodePulse automated code-review and technical-debt
platform. Consumes normalized PR/MR analysis jobs from RabbitMQ, runs a
fixed ESLint ruleset against the checked-out code, computes quality
metrics (complexity, cognitive complexity, code size, unused code, issue
density, per-rule and per-file statistics), and persists normalized
findings + metrics for `main-backend`/`web-interface` to read over HTTP.

```text
Backend  = analysis + calculation + persistence   (this service, main-backend)
Frontend = presentation + interaction             (web-interface)
```

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
                       ▼
                    ESLint
                       │
                       ▼
              Finding Normalizer
                       │
                       ▼
              Metrics Calculator
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
      PostgreSQL                 RabbitMQ
   (findings + metrics)             │
                                    ▼
                          analysis.completed
```

### Layers

| Layer | Responsibility |
|---|---|
| `api/` | FastAPI health/readiness + read routes (jobs arrive via RabbitMQ, not HTTP) |
| `consumers/` | Entry point for analysis jobs — the RabbitMQ equivalent of a controller |
| `application/` | Orchestrates the full pipeline (Pipeline/Command Pattern) |
| `domain/` | Internal models: `AnalysisJob`, `Finding`, `AnalysisResult`, `AnalysisMetrics` |
| `analyzers/` | ESLint adapter (Adapter Pattern) — parses ESLint's own `--format json` output directly |
| `factories/` | Selects the ESLint analyzer when JS/TS is detected — Factory Pattern |
| `metrics/` | Computes `AnalysisMetrics`/`RuleStatistic`/`FileStatistic` from findings + a workspace file scan |
| `workspace/` | Isolated per-job temp workspace, secure clone/checkout, cleanup |
| `repositories/` | Postgres persistence — Repository Pattern |
| `messaging/` | Publishes `analysis.completed`/`analysis.failed` |
| `infrastructure/`, `config.py` | Cross-cutting infrastructure (DB/RabbitMQ connections, settings) |

Each layer's `README.md` describes its responsibility in more detail.

## Local development

Requires Docker (for this service's own Postgres) and the **same shared
RabbitMQ broker `webhook-listener`'s `docker-compose.yml` provides** —
start that one first.

```bash
# 1. Start this service's own Postgres (its own DB, port 5434)
docker compose up -d

# 2. Create and activate a virtualenv, install Python dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Set up this service's own ESLint toolchain (never the analyzed
#    repository's own eslint/devDependencies — see analyzers/README.md)
cd tools/eslint && npm install && cd ../..

# 4. Copy env config (already matches docker-compose.yml's ports/credentials,
#    and assumes webhook-listener's shared RabbitMQ is already running)
cp .env.example .env

# 5. Start the service
uvicorn analysis_engine.main:app --reload --app-dir src --port 8000
```

```bash
curl http://localhost:8000/health   # liveness — process is up
curl http://localhost:8000/ready    # readiness — DB + RabbitMQ reachable
```

Read API, consumed by `main-backend`'s gateway (and, from there,
`web-interface`'s dashboard):

```bash
curl http://localhost:8000/api/repositories/{owner}/{repo}/analysis
curl http://localhost:8000/api/repositories/{owner}/{repo}/analysis/pull-requests/{number}
```

## Metrics

`AnalysisMetrics` (files analyzed, LOC, error/warning/issue counts and
densities, cyclomatic complexity, cognitive complexity, code size, unused
code) plus per-rule and per-file statistics are computed once, in
`metrics/calculator.py`, and stored on every `AnalysisResult`. No
consumer of this service's API recomputes them — see `metrics/README.md`
for exactly how each figure is derived from ESLint's rule-violation
output.

## Tests

```bash
pytest
```
