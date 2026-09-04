# analyzers

Tool adapters (Adapter Pattern via the `Analyzer` Strategy interface).
This service runs a single tool — ESLint — by design: it's a dedicated
JavaScript/TypeScript code-quality analyzer, not a generic multi-language
aggregator.

- `Analyzer` (ABC) — `analyze(workspace, job)` (takes `job`, not just
  `workspace`, because every `Finding` needs repository/PR/commit context
  that only `AnalysisJob` carries).
- **`EslintAnalyzer`** — runs against a fixed ruleset this service owns
  (`tools/eslint/`), **never** the analyzed repository's own ESLint
  config/devDependencies — running `npm install` against a
  repo-controlled `package.json` (arbitrary postinstall scripts) would
  undermine "never execute untrusted repository code directly on the
  host." Includes Node + browser globals so ordinary builtins
  (`console`, `require`, `window`, ...) aren't false-positived by
  `no-undef`. Parses ESLint's own `--format json` output directly — no
  external diagnostic-aggregation layer sits between ESLint and `Finding`
  construction.

Two non-obvious things worth knowing:
- **ESLint's nonzero exit code means "issues found," not "tool
  crashed."** `process_runner.py` deliberately doesn't decide
  success/failure by exit code itself — `EslintAnalyzer` treats 0 or 1 as
  success; anything else is a genuine tool/config failure.
- **`AnalysisMetrics` (complexity, cognitive complexity, code size) is
  derived from ESLint rule-violation *messages***, not from a separate
  complexity engine — see `../metrics/calculator.py` for the parsing and
  the accuracy tradeoff that implies (only violating functions/files
  contribute a measured value).

`fingerprint` is left as `""` on every Finding produced here — computed
by `application/finding_normalizer.py`, not here. `category` mapping
(`eslint_analyzer.py`'s `_RULE_CATEGORY_MAP`) is deliberately not
exhaustive across every eslint:recommended/typescript-eslint rule; it
covers the rules `AnalysisMetrics` depends on plus a handful of clearly
bug-shaped built-ins, and falls back to `code_smell` otherwise.
