# analyzers/python

Python (Pylint + Radon + Bandit) analysis — a self-contained module of
`analysis-engine`, not a separate service. Removing this whole directory
(and its `factories/language_detector.py` `.py` entry,
`factories/analyzer_factory.py`'s `PythonAnalyzer` registration, and
`metrics/python/`) leaves the ESLint/JS-TS analyzer fully working; nothing
outside this module imports anything Python-analysis-specific.

## Architecture

```text
PythonAnalyzer            (implements the top-level Analyzer interface)
    ├── PylintAnalyzer     — code quality
    ├── RadonAnalyzer      — complexity / maintainability / Halstead / LOC
    └── BanditAnalyzer     — security
```

`PythonAnalyzer` (`python_analyzer.py`) is the only piece the rest of the
service knows about — registered in `AnalyzerFactory` exactly like
`EslintAnalyzer`, selected when `factories/language_detector.py` detects
`.py` files. Internally it runs all three tools **concurrently**
(`asyncio.gather`) against the same file list, discovered once via
`discover_python_files()` and handed to all three rather than each tool
walking the workspace itself.

`PylintAnalyzer`/`RadonAnalyzer`/`BanditAnalyzer` are **not** themselves
`Analyzer` implementations — that interface returns a flat `list[Finding]`,
but each of these needs to report its own execution status (see below)
independently, so a `PythonAnalyzer` timing out on Bandit doesn't discard
real Pylint/Radon results. That richer detail is returned as a
`PylintRun`/`RadonRun`/`BanditRun` (`domain/python_metrics.py`) and
combined into `PythonAnalysisResult`, exposed on `PythonAnalyzer.last_result`
after `analyze()` runs — `AnalysisOrchestrator` reads it back (via a
duck-typed `getattr`, not a concrete import) to populate
`AnalysisResult.python`.

## Execution status

Each tool's own exit-code convention is different, and none of the three
treat "found issues" and "the tool crashed" the same way — confirmed by
running each pinned version directly, not assumed from documentation:

| Tool | Exit code meaning | Verified against |
|---|---|---|
| Pylint | Bitmask: 1=fatal, 2=error, 4=warning, 8=refactor, 16=convention, **32=usage error**. Only 32 (or unparseable stdout) is a real execution failure — everything else, including a syntax-error finding, is a normal result. | pylint 4.0.8 |
| Radon | **Always 0**, regardless of errors. Per-file failures (missing file, syntax error) appear as a `{"error": ...}` value in the JSON instead of a nonzero exit — turned into a Finding here (`radon-cc`/`radon-mi` rule IDs), not a crash. | radon 6.0.1 |
| Bandit | 0 = no issues, 1 = issues found (not a crash). Any other code is a real execution failure. Per-file parse errors land in the JSON's own `errors` array (bandit keeps going) — also turned into a Finding (`bandit-parse-error`). | bandit 1.9.4 |

Every adapter's `.run()` returns one of `AnalyzerRunStatus`
(`domain/analyzer_status.py`): `SUCCESS`, `COMPLETED_WITH_FINDINGS`,
`EXECUTION_ERROR`, or `TIMEOUT`. A missing/unexecutable binary
(`FileNotFoundError`/`OSError` from `asyncio.create_subprocess_exec`,
distinct from `process_runner.ToolExecutionError`, which only ever means
"timed out") is also caught and reported as `EXECUTION_ERROR`, not left
to crash the whole job.

`PythonAnalyzer.analyze()` only raises (making the composite itself count
as a failed analyzer, the same way one failed ESLint run would) if
**every** sub-tool ended in `EXECUTION_ERROR`/`TIMEOUT` — one tool failing
while the others succeed still returns their real findings.

## Raw output, never discarded

Every finding-level detail (Pylint's `type`/`obj`/`message-id`, Bandit's
`confidence`/CWE, Radon's per-function complexity/rank) is preserved —
either as a `Finding` field directly, or in `Finding.metadata` when it
doesn't fit the common shape. `RadonRun` additionally keeps the full raw
`complexity`/`maintainability`/`halstead`/`raw_loc` lists (see
`domain/python_metrics.py`), independent of which entries crossed a
threshold and became Findings. See `../../metrics/python/README.md` for
how the aggregate metrics get computed from all of this.

## Categorization

- Pylint: `type` → category (`fatal`/`error`→`bug`, `warning`/`refactor`→
  `code_smell`, `convention`→`style`) and → severity (`fatal`/`error`→
  `error`, `warning`/`refactor`→`warning`, `convention`→`info`).
- Radon: complexity findings → category `complexity`; maintainability
  findings → category `maintainability`.
- Bandit: always category `vulnerability` (reusing the existing
  `FindingCategory` value rather than adding a redundant `security` one).

## Installing the toolchain

```bash
pip install -e ".[dev]"   # pyproject.toml pins pylint==4.0.8, radon==6.0.1, bandit==1.9.4
```

Console scripts are resolved next to this project's own venv
(`config.py`'s `pylint_bin_path`/`radon_bin_path`/`bandit_bin_path`),
overridable via env vars for container/production deployments — see
`../../../Dockerfile`.

## Excluded directories

`factories/language_detector.py` (detection) and this module's own
`discover_python_files()` (file discovery for the actual subprocess
calls) both skip `.git`, `node_modules`, `dist`, `build`, `.venv`,
`venv`, `env`, `__pycache__` — kept in sync by hand since the two live at
different layers.
