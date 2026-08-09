# analyzers

Tool adapters (Adapter Pattern via the `Analyzer` Strategy interface) —
one per static-analysis tool.

- `Analyzer` (ABC) — the contract: `tool_name`, `supported_languages`,
  `supported_categories`, `reviewdog_format` (all static metadata),
  `supports(language)`, `build_command(workspace)`, and
  `analyze(workspace) -> list[Finding]`. Enforced at instantiation time —
  a subclass missing any abstract member raises `TypeError` immediately,
  not a runtime surprise later.
- `EslintAnalyzer`, `PylintAnalyzer`, `RadonAnalyzer`, `CppcheckAnalyzer`
  — all four fully declare their real metadata (languages, categories,
  Reviewdog format) as of Phase 5. `build_command`/`analyze` remain Phase
  6 stubs. **Reviewdog format strings for Radon (`rdjson`) and Cppcheck
  (`checkstyle`) are best-guess placeholders, not verified** — unlike
  ESLint/Pylint, which have confirmed built-in Reviewdog parsers. Phase 6
  needs to check these against Reviewdog's actual docs/`--help` before
  relying on them. Radon in particular may not fit Reviewdog's
  line-diagnostic model at all, since its output is per-function/file
  complexity scores, not line-anchored issues.

Reviewdog is the review/diagnostic aggregation and reporting layer sitting
behind these adapters — this service owns orchestration and result
processing; Reviewdog is not where business logic lives.
