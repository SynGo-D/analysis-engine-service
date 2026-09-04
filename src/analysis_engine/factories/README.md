# factories

- `language_detector.py` — `detect_languages(workspace_path)`: walks the
  checked-out repository and returns the set of languages present
  (`javascript`/`typescript`), via file-extension matching, skipping
  vendored/generated directories (`node_modules`, `.git`, `dist`,
  `build`, `coverage`) so a committed dependency tree doesn't cause the
  analyzer to run against code the repository owner doesn't actually
  own. A workspace with no JS/TS files at all detects no languages, and
  `AnalyzerFactory` then selects no analyzers — the orchestrator still
  completes the job, just with zero findings.
- `analyzer_factory.py` — `AnalyzerFactory.create_for_languages(languages)`
  (Factory Pattern): returns every analyzer (from `analyzers/`) applicable
  to the detected language set. Currently a single-tool factory
  (`EslintAnalyzer`, JS/TS), kept list-shaped rather than
  single-instance-returning so a second JS/TS-capable tool could be added
  later without changing this method's contract or the orchestrator that
  calls it.
