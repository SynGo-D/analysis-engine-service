# factories

- `language_detector.py` — `detect_languages(workspace_path)`: walks the
  checked-out repository and returns the set of languages present
  (`javascript`/`typescript`/`python`), via file-extension matching,
  skipping vendored/generated directories (`node_modules`, `.git`,
  `dist`, `build`, `coverage`, `.venv`, `venv`, `env`, `__pycache__`) so a
  committed dependency tree doesn't cause an analyzer to run against code
  the repository owner doesn't actually own. A workspace with no
  matching files at all detects no languages, and `AnalyzerFactory` then
  selects no analyzers — the orchestrator still completes the job, just
  with zero findings.
- `analyzer_factory.py` — `AnalyzerFactory.create_for_languages(languages)`
  (Factory Pattern): returns every analyzer (from `analyzers/`) applicable
  to the detected language set — `EslintAnalyzer` for JS/TS,
  `PythonAnalyzer` for Python, both selected the same way and (in a
  polyglot repository) run concurrently. Kept list-shaped rather than
  single-instance-returning so a second same-language tool could be added
  later without changing this method's contract or the orchestrator that
  calls it. Adding a whole new language means implementing `Analyzer`
  once and adding one line to `_ALL_ANALYZERS` — nothing else here, or in
  `application/orchestrator.py`, needs to change.
