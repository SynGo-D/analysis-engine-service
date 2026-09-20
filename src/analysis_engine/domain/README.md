# domain

Internal domain models (Pydantic `BaseModel`) — the shapes every other
layer operates on. No provider/tool-specific structure leaks in here; the
ESLint/Python analyzers and the RabbitMQ consumer are responsible for
translating into these shapes.

- `AnalysisJob` — the normalized job consumed from `pr_queue`. Field
  aliases mirror webhook-listener's `PRJob` message exactly (`cloneUrl`,
  `prNumber`, etc.) — no shared schema exists between the two repos, so
  this is kept in sync by hand.
- `Finding` — a single normalized issue from any tool (ESLint, Pylint,
  Radon, Bandit): repository, PR/MR number, commit SHA, file path,
  line/column/end_line/end_column, severity, category, rule ID, message,
  tool, a fingerprint used for commit-scoped deduplication, and a
  `metadata` dict for tool-specific extras that don't fit the common
  shape (Bandit's confidence/CWE, Pylint's message-id, ...) — see its own
  docstring. `fingerprint` is required — a `Finding` is, by construction,
  already the output of `FindingNormalizer`, not raw tool output.
- `analyzer_status.py` — `AnalyzerRunStatus` (`SUCCESS` /
  `COMPLETED_WITH_FINDINGS` / `EXECUTION_ERROR` / `TIMEOUT`): lets a tool
  report "ran and found nothing" separately from "ran and found issues"
  separately from "failed to run at all" — collapsing those into a single
  success/failure boolean is exactly what makes a nonzero exit code that
  actually means "found issues" (Pylint, Bandit) get misread as a crash.
- `metrics.py` — `AnalysisMetrics` (JS/TS: LOC, error/warning/issue
  counts and densities, cyclomatic complexity, cognitive complexity, code
  size, unused code) plus `RuleStatistic`/`FileStatistic` — computed once
  by `../metrics/calculator.py`.
- `python_metrics.py` — the Python equivalent, structured around three
  tools instead of one: raw shapes (`RadonComplexityEntry`,
  `RadonMaintainabilityEntry`, `RadonHalsteadEntry`, `RadonRawLocEntry`)
  preserved in full, per-tool aggregates (`PylintMetrics`,
  `RadonComplexityMetrics`, `MaintainabilityMetrics`, `HalsteadMetrics`,
  `BanditMetrics`) computed by `../metrics/python/calculator.py`, and
  per-tool run results (`PylintRun`/`RadonRun`/`BanditRun`, each carrying
  its own `AnalyzerRunStatus`) combined into `PythonAnalysisResult`.
- `AnalysisResult` — the aggregate outcome of one job: status
  (`completed`/`failed`), `findings` (every tool's, mixed together —
  what a findings explorer would page through), the JS/TS-specific
  `metrics`/`rule_statistics`/`file_statistics`, and `python` (`None`
  when no Python was in the workspace at all, populated otherwise —
  see `python_metrics.py`).

Designed so a new language's finding/metric fields (`python` mirrors this
exactly) can be added without changing the orchestration/consumer layers
that pass these objects around, and so removing a language later means
deleting its own model file(s), not editing this one.
