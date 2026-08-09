# domain

Internal domain models (Pydantic `BaseModel`) — the shapes every other
layer operates on. No provider/tool-specific structure leaks in here;
analyzers and the RabbitMQ consumer are responsible for translating into
these shapes.

- `AnalysisJob` — the normalized job consumed from `pr_queue`. Field
  aliases mirror webhook-listener's `PRJob` message exactly (`cloneUrl`,
  `prNumber`, etc.) — no shared schema exists between the two repos, so
  this is kept in sync by hand.
- `Finding` — a single normalized issue: repository, PR/MR number, commit
  SHA, file path, optional line/column (some tools like Radon report
  per-function/per-file, not per-line), severity, category, rule ID,
  message, tool, fingerprint, and an optional per-issue SQALE remediation
  cost. `fingerprint` is required — a `Finding` is, by construction,
  already the output of Phase 7's normalization step, not raw tool output.
- `AnalysisResult` — the aggregate outcome of one job: status
  (`completed`/`failed`), findings, a `TechnicalDebtSummary` (SQALE
  *inputs*, not a final rating — that's a downstream service's job), and
  timing.

Designed so new finding fields or job types can be added without changing
the orchestration/consumer layers that pass these objects around.
