import asyncpg

# Phase 9 — persistence. No migration tool exists yet for this service
# (unlike the Node services' node-pg-migrate), so this mirrors their
# migrations' own idempotent style (CREATE TABLE IF NOT EXISTS) as a single
# statement run once at startup, rather than introducing a separate
# migration framework for two tables.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS analysis_results (
    result_id            UUID PRIMARY KEY,
    job_id                UUID NOT NULL,

    repository            TEXT NOT NULL,
    pull_request_number   INTEGER NOT NULL,
    commit_sha            TEXT NOT NULL,

    status                VARCHAR(20) NOT NULL CHECK (status IN ('completed', 'failed')),
    error_message         TEXT,

    started_at            TIMESTAMPTZ NOT NULL,
    completed_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
