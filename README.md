# Analysis Engine

A code-quality analyzer microservice for the CodePulse automated
code-review and technical-debt platform — ESLint for JavaScript/TypeScript,
Pylint/Radon/Bandit for Python. Consumes normalized PR/MR analysis jobs
from RabbitMQ, runs whichever analyzers apply to the languages detected in
the checked-out code, computes quality metrics per language, and persists
normalized findings + metrics for `main-backend`/`web-interface` to read
over HTTP.

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
          ┌────────────┴────────────┐
          ▼                         ▼
       ESLint                 PythonAnalyzer
      (JS/TS)         ┌──────────┼──────────┐
                       ▼          ▼          ▼
                   Pylint      Radon      Bandit
                       │          │          │
                       └──────────┴──────────┘
                                  │
                                  ▼
                       Finding Normalizer
                                  │
                                  ▼
                       Metrics Calculator
                        (per language)
                                  │
          ┌───────────────────────┴────────────────────────┐
          ▼                                                 ▼
      PostgreSQL                                         RabbitMQ
   (findings + metrics, JS/TS and Python)                    │
                                                              ▼
                                                    analysis.completed
```

### Layers

| Layer | Responsibility |
|---|---|
| `api/` | FastAPI health/readiness + read routes (jobs arrive via RabbitMQ, not HTTP) |
| `consumers/` | Entry point for analysis jobs — the RabbitMQ equivalent of a controller |
| `application/` | Orchestrates the full pipeline (Pipeline/Command Pattern) |
| `domain/` | Internal models: `AnalysisJob`, `Finding`, `AnalyzerRunStatus`, `AnalysisResult`, `AnalysisMetrics`, `PythonAnalysisResult` |
| `analyzers/` | `EslintAnalyzer` (JS/TS) + `analyzers/python/` (`PythonAnalyzer` over Pylint/Radon/Bandit) — Adapter Pattern |
| `factories/` | Selects the applicable analyzer(s) per detected language — Factory Pattern |
| `metrics/` | Computes JS/TS `AnalysisMetrics`/`RuleStatistic`/`FileStatistic` and (`metrics/python/`) the Python equivalents, from findings + raw tool output |
| `workspace/` | Isolated per-job temp workspace, secure clone/checkout, cleanup |
| `repositories/` | Postgres persistence — Repository Pattern |
| `messaging/` | Publishes `analysis.completed`/`analysis.failed` |
| `infrastructure/`, `config.py` | Cross-cutting infrastructure (DB/RabbitMQ connections, settings) |

Each layer's `README.md` describes its responsibility in more detail —
`analyzers/python/README.md` and `metrics/python/README.md` specifically
document the Python support end-to-end (exit-code semantics per tool,
threshold constants, the two-pass density calculation, removal notes).

## Local development

Requires Docker (for this service's own Postgres) and the **same shared
RabbitMQ broker `webhook-listener`'s `docker-compose.yml` provides** —
start that one first. (Everything below can also just run inside a
container instead — see `Dockerfile`.)

```bash
# 1. Start this service's own Postgres (its own DB, port 5434)
docker compose up -d postgres

# 2. Create and activate a virtualenv, install Python dependencies
#    (includes Pylint, Radon, Bandit — pinned versions, see pyproject.toml)
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

## Analyzer versions

Pinned in `pyproject.toml` (Python) / `tools/eslint/package.json` (JS):

```text
ESLint:  ^9.0.0 (+ typescript-eslint ^8.66.0, eslint-plugin-sonarjs ^2.0.0)
Pylint:  4.0.8
Radon:   6.0.1
Bandit:  1.9.4
```

## Metrics

Every quality metric — `AnalysisMetrics` (JS/TS: LOC, error/warning/issue
counts and densities, cyclomatic complexity, cognitive complexity, code
size, unused code) and `PythonMetrics` (Python: LOC/KLOC sourced from
Radon's own `raw` output, Pylint issue density, cyclomatic complexity,
maintainability index, Bandit security-issue counts/density, Halstead
volume/difficulty/effort) — is computed exactly once, in `metrics/` /
`metrics/python/`, and stored on `AnalysisResult`. No consumer of this
service's API (`main-backend`, `web-interface`) recomputes any of it —
see `metrics/README.md` / `metrics/python/README.md` for exactly how each
figure is derived.

## Example result (Python)

```json
{
  "repository": "owner/repo", "pull_request_number": 42, "status": "completed",
  "findings": [
    {"tool": "pylint", "rule_id": "unused-import", "severity": "warning", "category": "code_smell", "file_path": "src/db.py", "line": 1, "message": "Unused import os"},
    {"tool": "radon", "rule_id": "radon-cyclomatic-complexity", "severity": "warning", "category": "complexity", "file_path": "src/service.py", "line": 42, "message": "Function 'process_data' has a cyclomatic complexity of 13 (rank C)."},
    {"tool": "bandit", "rule_id": "B105", "severity": "info", "category": "vulnerability", "file_path": "config.py", "line": 15, "message": "Possible hardcoded password: 'hunter2'", "metadata": {"confidence": "MEDIUM"}}
  ],
  "python": {
    "pylint": {"status": "completed_with_findings", "findings": ["..."], "metrics": {"total_issues": 28, "issues_per_kloc": 114.29}},
    "radon":  {"status": "completed_with_findings", "findings": ["..."], "complexity": ["..."], "complexity_metrics": {"average_complexity": 4.59, "maximum_complexity": 13}},
    "bandit": {"status": "completed_with_findings", "findings": ["..."], "metrics": {"total_issues": 5, "high_severity": 1}},
    "metrics": {
      "loc": {"physical_loc": 245, "kloc": 0.245},
      "pylint_issue_density": 114.29,
      "average_cyclomatic_complexity": 4.59, "maximum_cyclomatic_complexity": 13,
      "average_maintainability_index": 64.87, "low_maintainability_file_count": 2,
      "bandit_issue_count": 5, "security_issue_density": 20.41
    }
  }
}
```

(Full, real output — 75-test-suite-verified against
`tests/fixtures/python_project/` — is longer; this is trimmed for
readability.)

## Tests

```bash
pytest                          # everything, including real subprocess integration tests
pytest -m "not integration"     # unit tests only — no eslint/pylint/radon/bandit binaries needed
```

`tests/fixtures/python_project/` is a small fixture Python repository with
one file per concern (clean, Pylint violations, high complexity, low
maintainability, Bandit-detectable security issues) — see
`analyzers/python/README.md` for what it exercises and
`tests/test_python_analyzer.py`/`tests/test_repository_python_persistence.py`
for the integration/persistence tests that run the real tools against it.

### Adding another Python analyzer

1. Implement a `<Tool>Analyzer` in `analyzers/python/` following
   `bandit_analyzer.py`'s shape: a `run(workspace, job, python_files) -> <Tool>Run`
   method returning an `AnalyzerRunStatus`, findings, and that tool's own
   metrics (add the `<Tool>Run`/`<Tool>Metrics` models to
   `domain/python_metrics.py`).
2. Add a `calculate_<tool>_metrics()` to `metrics/python/calculator.py`.
3. Wire it into `PythonAnalyzer.analyze()` (`python_analyzer.py`) —
   add it to the `asyncio.gather(...)` alongside Pylint/Radon/Bandit, and
   fold its metrics into `PythonAnalysisResult`/`calculate_python_metrics()`.

Nothing outside `analyzers/python/`/`metrics/python/`/
`domain/python_metrics.py` needs to change — `AnalyzerFactory`,
`AnalysisOrchestrator`, and the ESLint analyzer stay untouched.
