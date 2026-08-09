# factories

- `language_detector.py` — `detect_languages(workspace_path)`: walks the
  checked-out repository and returns the set of languages present, via
  file-extension matching (deliberately simple, not a content-based
  classifier), skipping vendored/generated directories (`node_modules`,
  `.git`, `__pycache__`, etc.) so a committed dependency tree doesn't
  cause analyzers to run against code the repository owner doesn't
  actually own.
- `analyzer_factory.py` — `AnalyzerFactory.create_for_languages(languages)`
  (Factory Pattern): returns every analyzer (from `analyzers/`) applicable
  to the detected language set — not just one, since multiple tools can
  target the same language (Pylint and Radon both run on Python). Adding
  a new tool/language means implementing `Analyzer` once and adding it to
  the factory's list — nothing in `application/`'s orchestrator ever
  branches on language or tool itself.
