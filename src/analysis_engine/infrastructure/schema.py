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

    -- PythonAnalysisResult (see domain/python_metrics.py) — NULL, not an
    -- empty object, when the workspace had no Python at all (distinct
    -- from "Python was analyzed and every tool failed").
    python_result         JSONB,

    started_at            TIMESTAMPTZ NOT NULL,
    completed_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS branch TEXT NOT NULL DEFAULT '';
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS metrics JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS rule_statistics JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS file_statistics JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS python_result JSONB;

-- PullRequestChanges (see domain/change_set.py): what the PR itself
-- changed. NULL for results stored before this column existed.
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS pull_request_changes JSONB;

-- Who opened the pull request. Nullable rather than defaulted: rows
-- written before this existed genuinely have no author, and an empty
-- string would be indistinguishable from a real one.
-- Milliseconds per pipeline stage. JSONB rather than a column per stage:
-- the stages change as the pipeline does, and these are read back whole
-- for one job or aggregated by key, never filtered on individually.
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS timings JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS author_username TEXT;
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS author_provider_id TEXT;

-- The contributor page groups one repository's analyses by author, which
-- is exactly this index. Partial, because rows with no author are never
-- grouped and there is no reason to carry them in it.
CREATE INDEX IF NOT EXISTS idx_analysis_results_repository_author
    ON analysis_results (repository, author_username)
    WHERE author_username IS NOT NULL;

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
    end_line                INTEGER,
    end_col                 INTEGER,

    severity                VARCHAR(10) NOT NULL,
    category                VARCHAR(30) NOT NULL,
    rule_id                 TEXT NOT NULL,
    message                 TEXT NOT NULL,
    tool                    VARCHAR(30) NOT NULL,
    fingerprint             TEXT NOT NULL,
    remediation_minutes     INTEGER,

    -- Tool-specific extras that don't fit the columns above (e.g.
    -- Bandit's confidence/CWE, Pylint's message-id) — see
    -- domain/finding.py's own docstring for why this exists instead of
    -- forcing everything into generic columns.
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE findings ADD COLUMN IF NOT EXISTS end_line INTEGER;
ALTER TABLE findings ADD COLUMN IF NOT EXISTS end_col INTEGER;
ALTER TABLE findings ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_findings_result
    ON findings (result_id);

-- One AI review per analysis result (see domain/agent_review.py). The
-- review is stored whole as JSONB: it's written and read as a unit, never
-- queried field by field. status and cost are columns too, for operations
-- ("how many reviews failed today", "what did reviews cost this month")
-- without unpacking JSON.
CREATE TABLE IF NOT EXISTS agent_reviews (
    review_id             UUID PRIMARY KEY,
    result_id             UUID NOT NULL UNIQUE
        REFERENCES analysis_results (result_id) ON DELETE CASCADE,
    repository            TEXT NOT NULL,
    pull_request_number   INTEGER NOT NULL,
    status                VARCHAR(20) NOT NULL
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    cost_usd              NUMERIC(12, 6),
    review                JSONB NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_reviews_repo
    ON agent_reviews (repository, pull_request_number, created_at DESC);

-- Developer feedback on AI review issues (docs/agent-architecture.md §12,
-- "the live signal"). Keyed by the issue's fingerprint, which ignores line
-- numbers, so feedback follows an issue across pushes to the same PR. One
-- verdict per user per issue: changing your mind replaces it.
CREATE TABLE IF NOT EXISTS agent_finding_feedback (
    repository    TEXT NOT NULL,
    fingerprint   VARCHAR(64) NOT NULL,
    user_id       TEXT NOT NULL,
    verdict       VARCHAR(12) NOT NULL CHECK (verdict IN ('useful', 'not_useful', 'wrong')),
    note          TEXT,
    pull_request_number INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repository, fingerprint, user_id)
);

-- Business rules stored per repository (see domain/business_rule.py):
-- added in the dashboard, or suggested by the Rule Miner. Rules from a
-- repository's own .codepulse/rules.yml are not stored; they're read from
-- the target branch at review time.
CREATE TABLE IF NOT EXISTS business_rules (
    repository   TEXT NOT NULL,
    rule_id      VARCHAR(40) NOT NULL,
    rule         TEXT NOT NULL,
    applies_to   JSONB NOT NULL DEFAULT '[]'::jsonb,
    severity     VARCHAR(10) NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    rationale    TEXT,
    source       VARCHAR(20) NOT NULL CHECK (source IN ('dashboard', 'suggested')),
    status       VARCHAR(20) NOT NULL CHECK (status IN ('active', 'suggested', 'rejected')),
    evidence     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repository, rule_id)
);
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA_SQL)
