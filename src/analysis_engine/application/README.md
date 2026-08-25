# application

Use cases / orchestration — the core pipeline:

```
validate job → obtain repository → create workspace → checkout commit
  → detect languages → select analyzers → execute via Reviewdog
  → normalize findings → calculate quality/technical-debt metrics
  → persist results → publish completion event
```

`AnalysisOrchestrator` is the only place that sequence is encoded
(Pipeline/Command Pattern). It coordinates `workspace/`, `factories/`,
`analyzers/`, `repositories/`, and `messaging/` — none of those layers
know about each other directly, only about the orchestrator. As of this
phase, wired up through normalization (workspace prep → language
detection → analyzer selection → concurrent analyzer execution →
`FindingNormalizer`) — the first point the pipeline runs end-to-end as
one flow. `technical_debt` calculation (Phase 8), persistence (Phase 9),
and publishing the completion event (Phase 10) remain outstanding;
`AnalysisResult.technical_debt` is still the all-zero default until then.

Partial-failure policy: analyzers run concurrently and independently — if
some fail while others succeed, the job still completes with whatever
findings the successful ones produced (a single tool's failure shouldn't
discard real results from the rest). Only if *every* selected analyzer
fails does the job report `status="failed"`.

`FindingNormalizer` computes each Finding's real fingerprint (analyzers
themselves leave it as `""`, see `analyzers/README.md`) and deduplicates
by it — commit-scoped, not tracking one logical issue's identity across
different commits (a materially harder problem, not attempted here). See
its own docstring for exactly what does and doesn't go into the hash.
