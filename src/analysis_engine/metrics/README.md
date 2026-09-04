# metrics

Computes `AnalysisMetrics`/`RuleStatistic`/`FileStatistic` (see
`../domain/metrics.py`) — the only place in this service that does this
calculation. `AnalysisOrchestrator` calls both pieces here once per job
and stores the result on `AnalysisResult`; every consumer downstream
(main-backend's gateway, web-interface's dashboard) only ever displays
these already-computed values.

- `file_scanner.py` — `scan_js_ts_files(workspace_path)`: walks the
  checked-out workspace and maps every JS/TS file to its line count,
  independent of ESLint's own output. Must run while the workspace still
  exists (inside `WorkspaceManager.prepare()`'s `async with` block).
- `calculator.py` — `calculate_metrics`, `calculate_rule_statistics`,
  `calculate_file_statistics`: pure functions over `findings` (already
  normalized/deduplicated) plus the file→LOC map above. Complexity/
  cognitive-complexity/largest-function figures are parsed directly out
  of the relevant ESLint rule's violation message text (there's no
  separate complexity engine) — see each regex's neighboring comment for
  the exact message format it expects, which is pinned to the ruleset in
  `../../tools/eslint/eslint.config.cjs`. This means those figures only
  reflect functions/files that *exceeded* their threshold; a file with no
  violations reports `0`/`None`, not an unmeasured value.
