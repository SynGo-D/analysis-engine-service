# repositories

Data-access layer (Repository Pattern) — direct database driver only
(`asyncpg` for Postgres), no ORM, matching the platform's existing
convention. `AnalysisResultRepository` is the only place in this service
that issues SQL against `analysis_results`/`findings`.

`metrics`/`rule_statistics`/`file_statistics` (see `../domain/metrics.py`)
and `python_result` (`PythonAnalysisResult`, see
`../domain/python_metrics.py` — `NULL` when the job had no Python at all)
are stored as JSONB on `analysis_results` — read back whole as one
analysis run's aggregates, never queried/filtered by a specific metric
field at the SQL level, so normalizing them into further columns/tables
wouldn't earn its cost. asyncpg returns JSONB columns as raw JSON text (no
codec registered), so `_map_result` parses them back into their Pydantic
models explicitly rather than trusting bare dicts.

`findings.metadata` is JSONB too — tool-specific extras (Bandit's
confidence/CWE, Pylint's message-id, ...) that don't fit the shared
columns, per `Finding`'s own docstring; `findings.end_line`/`end_col`
persist the range info Pylint (and, in principle, any future tool)
reports beyond a single line/column.

Findings are written inside the same transaction as their parent result —
a result row should never exist without the findings it claims to have,
and vice versa.
