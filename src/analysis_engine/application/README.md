# application

Use cases / orchestration — the core pipeline:

```
validate job → obtain repository → create workspace → checkout commit
  → detect languages → select analyzers → run ESLint
  → normalize findings → calculate metrics → persist results
  → publish completion event
```

`AnalysisOrchestrator` is the only place that sequence is encoded
(Pipeline/Command Pattern). It coordinates `workspace/`, `factories/`,
`analyzers/`, and `metrics/` directly, and returns a fully-populated
`AnalysisResult` (findings + `metrics` + `rule_statistics` +
`file_statistics`) — persistence (`repositories/`) and publishing the
completion event (`messaging/`) happen in the caller (`consumers/`), not
here.

Partial-failure policy: if ESLint fails, the job reports
`status="failed"` with `error_message` set. `metrics.loc`/
`files_analyzed` are still populated even in that case — they come from
an independent workspace scan (`metrics/file_scanner.py`), not from
ESLint's own output, so a tool crash doesn't lose them.

`FindingNormalizer` computes each Finding's real fingerprint (the
analyzer itself leaves it as `""`, see `analyzers/README.md`) and
deduplicates by it — commit-scoped, not tracking one logical issue's
identity across different commits (a materially harder problem, not
attempted here). See its own docstring for exactly what does and doesn't
go into the hash.
