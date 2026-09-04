# domain

Internal domain models (Pydantic `BaseModel`) — the shapes every other
layer operates on. No provider/tool-specific structure leaks in here; the
ESLint analyzer and the RabbitMQ consumer are responsible for translating
into these shapes.

- `AnalysisJob` — the normalized job consumed from `pr_queue`. Field
  aliases mirror webhook-listener's `PRJob` message exactly (`cloneUrl`,
  `prNumber`, etc.) — no shared schema exists between the two repos, so
  this is kept in sync by hand.
- `Finding` — a single normalized ESLint issue: repository, PR/MR number,
  commit SHA, file path, line/column, severity, category, rule ID,
  message, tool, and a fingerprint used for commit-scoped deduplication.
  `fingerprint` is required — a `Finding` is, by construction, already the
  output of `FindingNormalizer`, not raw tool output.
- `metrics.py` — `AnalysisMetrics` (files analyzed, LOC, error/warning/
  issue counts and densities, cyclomatic complexity, cognitive complexity,
  code size, unused code) plus `RuleStatistic` and `FileStatistic` — the
  per-run aggregates this service computes once (see
  `../metrics/calculator.py`) and every consumer (main-backend,
  web-interface) only ever displays.
- `AnalysisResult` — the aggregate outcome of one job: status
  (`completed`/`failed`), `findings`, `metrics`, `rule_statistics`,
  `file_statistics`, and timing.

Designed so new finding/metric fields can be added without changing the
orchestration/consumer layers that pass these objects around.
