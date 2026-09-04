import asyncpg

# No migration tool exists for this service (unlike the Node services'
# node-pg-migrate), so this mirrors their migrations' own idempotent
# style: CREATE TABLE IF NOT EXISTS for a fresh database, plus ALTER
# TABLE ... ADD COLUMN IF NOT EXISTS so an already-running deployment
# picks up new columns (metrics/rule_statistics/file_statistics) without
# a separate migration step.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analysis_results (
    result_id            UUID PRIMARY KEY,
    job_id                UUID NOT NULL,

    repository            TEXT NOT NULL,
    pull_request_number   INTEGER NOT NULL,
    commit_sha            TEXT NOT NULL,
    branch                TEXT NOT NULL DEFAULT '',

    status                VARCHAR(20) NOT NULL CHECK (status IN ('completed', 'failed')),
    error_message         TEXT,

    -- AnalysisMetrics / list[RuleStatistic] / list[FileStatistic] (see
    -- domain/metrics.py), stored as-computed by metrics/calculator.py.
    -- JSONB rather than normalized columns/tables: these are read back
    -- whole (one analysis run's aggregates), never queried/filtered by
    -- a specific metric field at the SQL level.
    metrics               JSONB NOT NULL DEFAULT '{}'::jsonb,
    rule_statistics       JSONB NOT NULL DEFAULT '[]'::jsonb,
    file_statistics       JSONB NOT NULL DEFAULT '[]'::jsonb,

    started_at            TIMESTAMPTZ NOT NULL,
    completed_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS branch TEXT NOT NULL DEFAULT '';
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS metrics JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS rule_statistics JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS file_statistics JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_analysis_results_repo
    ON analysis_results (repository, pull_request_number, created_at DESC);

CREATE TABLE IF NOT EXISTS findings (
    finding_id            UUID PRIMARY KEY,
    result_id             UUID NOT NULL
        REFERENCES analysis_results (result_id) ON DELETE CASCADE,

    repository            TEXT NOT NULL,
    pull_request_number    INTEGER NOT NULL,
    commit_sha             TEXT NOT NULL,

    file_path              TEXT NOT NULL,
    line                    INTEGER,
    col                     INTEGER,

    severity                VARCHAR(10) NOT NULL,
    category                VARCHAR(30) NOT NULL,
    rule_id                 TEXT NOT NULL,
    message                 TEXT NOT NULL,
    tool                    VARCHAR(30) NOT NULL,
    fingerprint             TEXT NOT NULL,
    remediation_minutes     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_findings_result
    ON findings (result_id);
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA_SQL)
