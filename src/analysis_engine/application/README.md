# application

Use cases / orchestration — the core pipeline:

```
validate job → obtain repository → create workspace → checkout commit
  → detect languages → select analyzers → run ESLint/Python analyzers
  → normalize findings → calculate metrics → persist results
  → publish completion event
```

`AnalysisOrchestrator` is the only place that sequence is encoded
(Pipeline/Command Pattern). It coordinates `workspace/`, `factories/`,
`analyzers/`, and `metrics/` directly, and returns a fully-populated
`AnalysisResult` (findings + `metrics`/`rule_statistics`/`file_statistics`
+ `python`) — persistence (`repositories/`) and publishing the completion
event (`messaging/`) happen in the caller (`consumers/`), not here.
Doesn't special-case language: a polyglot repository runs `EslintAnalyzer`
and `PythonAnalyzer` concurrently through the exact same
`asyncio.gather(...)` this loop would use for any two analyzers.

`AnalysisResult.python` comes from `_extract_python_result()` — a
duck-typed `getattr(analyzer, "last_result", None)` over whichever
analyzers ran, not a concrete `PythonAnalyzer` import, so a future
language analyzer wanting the same "richer detail beyond a flat
`list[Finding]`" escape hatch can reuse it without this orchestrator
needing to know its type.

Partial-failure policy: if every analyzer for a language fails, the job
reports `status="failed"` with `error_message` set — but that's an
all-or-nothing policy across *every selected analyzer*, not per language;
`PythonAnalyzer` applies the same "all sub-tools failed" policy one level
down internally first (see `analyzers/python/README.md`), so a single
failed Pylint run alongside working Radon/Bandit runs never surfaces as
"the job failed" at all. `metrics.loc`/`files_analyzed` (JS/TS) and
`python.metrics.loc` are populated independent of whether their
respective tools' own findings-producing runs succeeded — the former from
an independent workspace scan (`metrics/file_scanner.py`), the latter
from Radon's own `raw` command (`metrics/python/calculator.py`) — so a
tool crash doesn't lose LOC data that didn't depend on it.

`FindingNormalizer` computes each Finding's real fingerprint (every
analyzer leaves it as `""`, see `analyzers/README.md`) and deduplicates
by it — commit-scoped, not tracking one logical issue's identity across
different commits (a materially harder problem, not attempted here). See
its own docstring for exactly what does and doesn't go into the hash.
