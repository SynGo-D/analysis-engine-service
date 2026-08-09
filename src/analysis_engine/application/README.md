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
know about each other directly, only about the orchestrator. Currently a
stub (`run()` raises) — `consumers/PRQueueConsumer` already calls it for
real, so each following phase's implementation just replaces a piece of
this stub without the consumer changing at all.

Planned: filled in incrementally alongside Phases 4-10 as each pipeline
stage is built.
