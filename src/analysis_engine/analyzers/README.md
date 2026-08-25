# analyzers

Tool adapters (Adapter Pattern via the `Analyzer` Strategy interface) —
one per static-analysis tool. All four fully implemented and verified
end-to-end against real tools and deliberately broken fixture code.

- `Analyzer` (ABC) — `analyze(workspace, job)` (note: takes `job` too, not
  just `workspace` — a Phase 6 correction to Phase 5's interface, caught
  while implementing: every `Finding` needs repository/PR/commit context
  that only `AnalysisJob` carries, not `Workspace`, same class of fix as
  webhook-listener's `deliveryId` parameter).
- **`EslintAnalyzer`** — the one tool that genuinely routes through
  Reviewdog (`-f=eslint`, confirmed via `reviewdog -list` as a real
  built-in parser). Runs against a fixed ruleset this service owns
  (`tools/eslint/`), **never** the analyzed repository's own ESLint
  config/devDependencies — running `npm install` against a
  repo-controlled `package.json` (arbitrary postinstall scripts) would
  undermine "never execute untrusted repository code directly on the
  host." Includes Node + browser globals so ordinary builtins
  (`console`, `require`, `window`, ...) aren't false-positived by
  `no-undef`.
- **`PylintAnalyzer`**, **`RadonAnalyzer`**, **`CppcheckAnalyzer`** — all
  three bypass Reviewdog entirely and parse their own structured output
  (JSON/JSON/XML) directly. Confirmed via `reviewdog -list`: none of the
  three has a real built-in Reviewdog parser — Phase 5's Pylint guess was
  wrong outright, Radon's/Cppcheck's placeholders were honest guesses
  that also turned out wrong. Radon's output (per-function/file
  complexity and maintainability *scores*) doesn't fit Reviewdog's
  line-diagnostic model anyway.

Two non-obvious things caught only by direct testing, not assumed:
- **Cppcheck writes its `--xml` report to *stderr*, not stdout** (stdout
  only gets progress lines). Reading stdout would have silently produced
  zero findings on every run.
- **Pylint/ESLint's nonzero exit codes mean "issues found," not "tool
  crashed."** `process_runner.py` deliberately doesn't decide
  success/failure by exit code itself — each analyzer interprets its own
  tool's conventions (ESLint: 0 or 1 is fine; Pylint: a bitmask where only
  the fatal/usage-error bits mean real failure; Cppcheck/Radon: 0 means
  fine, anything else is a real failure).

`fingerprint` is left as `""` on every Finding produced here — a
deliberate, honestly-labeled placeholder, not an oversight. What should
and shouldn't count toward a stable fingerprint (e.g. should line number
matter, given code shifting up/down shouldn't register as a "new" issue?)
is a real design question that belongs to Phase 7, not a decision rushed
inline while standing up four tool integrations at once. `category`
mappings are similarly coarse (tool-output-type-level, not per-rule) and
will be refined there too.

Reviewdog is the review/diagnostic aggregation and reporting layer where
it's actually a good fit (ESLint); this service owns orchestration and
result processing everywhere else, rather than forcing every tool through
Reviewdog's parsing when its own native output is more reliable.
