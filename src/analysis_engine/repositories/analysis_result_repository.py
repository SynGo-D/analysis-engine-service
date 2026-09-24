import json

import asyncpg

from .agent_review_repository import AgentReviewRepository
from ..domain import (
    AnalysisMetrics,
    AnalysisResult,
    Finding,
    FileStatistic,
    PullRequestChanges,
    PythonAnalysisResult,
    RuleStatistic,
)


class AnalysisResultRepository:
    """
    Data-access layer for `analysis_results`/`findings` — mirrors the Node
    services' Repository pattern (e.g. ProcessedWebhookEventRepository):
    the only place in this service that issues SQL against these two
    tables.

    Findings are written inside the same transaction as their parent
    result — a result row should never exist without the findings it
    claims to have, and vice versa.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        # Reviews live in their own table; results are returned with their
        # review attached so the API response carries both.
        self._reviews = AgentReviewRepository(pool)

    async def save(self, result: AnalysisResult) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO analysis_results (
                        result_id, job_id, repository, pull_request_number,
                        commit_sha, branch, status, error_message, metrics,
                        rule_statistics, file_statistics, python_result,
                        pull_request_changes, started_at, completed_at,
                        author_username, author_provider_id, timings
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
                    ON CONFLICT (result_id) DO NOTHING;
                    """,
                    result.result_id,
                    result.job_id,
                    result.repository,
                    result.pull_request_number,
                    result.commit_sha,
                    result.branch,
                    result.status,
                    result.error_message,
                    result.metrics.model_dump_json(),
                    json.dumps([s.model_dump() for s in result.rule_statistics]),
                    json.dumps([s.model_dump() for s in result.file_statistics]),
                    result.python.model_dump_json() if result.python else None,
                    result.changes.model_dump_json() if result.changes else None,
                    result.started_at,
                    result.completed_at,
                    result.author_username,
                    result.author_provider_id,
                    json.dumps(result.timings),
                )

                if result.findings:
                    await conn.executemany(
                        """
                        INSERT INTO findings (
                            finding_id, result_id, repository, pull_request_number,
                            commit_sha, file_path, line, col, end_line, end_col,
                            severity, category, rule_id, message, tool,
                            fingerprint, remediation_minutes, metadata
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18);
                        """,
                        [
                            (
                                finding.finding_id,
                                result.result_id,
                                finding.repository,
                                finding.pull_request_number,
                                finding.commit_sha,
                                finding.file_path,
                                finding.line,
                                finding.column,
                                finding.end_line,
                                finding.end_column,
                                finding.severity,
                                finding.category,
                                finding.rule_id,
                                finding.message,
                                finding.tool,
                                finding.fingerprint,
                                finding.remediation_minutes,
                                json.dumps(finding.metadata),
                            )
                            for finding in result.findings
                        ],
                    )

    async def list_for_repository(self, repository: str, limit: int = 20) -> list[AnalysisResult]:
        """
        Most-recent-first results (each with its findings attached) for a
        repository. Findings are fetched per-result rather than one large
        join — simpler to map back correctly, and the result count per
        repository is small enough (bounded by `limit`) that this isn't a
        meaningful cost at this scale.
        """
        async with self._pool.acquire() as conn:
            result_rows = await conn.fetch(
                """
                SELECT * FROM analysis_results
                WHERE repository = $1
                ORDER BY created_at DESC
                LIMIT $2;
                """,
                repository,
                limit,
            )

            results: list[AnalysisResult] = []
            for row in result_rows:
                finding_rows = await conn.fetch(
                    """SELECT * FROM findings WHERE result_id = $1 ORDER BY file_path, line;""",
                    row["result_id"],
                )
                findings = [self._map_finding(f) for f in finding_rows]
                results.append(self._map_result(row, findings=findings))

        reviews = await self._reviews.get_for_results([r.result_id for r in results])
        for result in results:
            result.review = reviews.get(result.result_id)
        return results

    async def get_latest_for_pull_request(
        self, repository: str, pull_request_number: int
    ) -> AnalysisResult | None:
        """The most recent full result (including findings) for one PR."""
        async with self._pool.acquire() as conn:
            result_row = await conn.fetchrow(
                """
                SELECT * FROM analysis_results
                WHERE repository = $1 AND pull_request_number = $2
                ORDER BY created_at DESC
                LIMIT 1;
                """,
                repository,
                pull_request_number,
            )

            if result_row is None:
                return None

            finding_rows = await conn.fetch(
                """SELECT * FROM findings WHERE result_id = $1 ORDER BY file_path, line;""",
                result_row["result_id"],
            )

        findings = [self._map_finding(row) for row in finding_rows]
        result = self._map_result(result_row, findings=findings)
        result.review = (await self._reviews.get_for_results([result.result_id])).get(result.result_id)
        return result

    # -------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------

    def _map_result(self, row: asyncpg.Record, findings: list[Finding]) -> AnalysisResult:
        return AnalysisResult(
            result_id=row["result_id"],
            job_id=row["job_id"],
            repository=row["repository"],
            pull_request_number=row["pull_request_number"],
            commit_sha=row["commit_sha"],
            branch=row["branch"],
            author_username=row["author_username"],
            author_provider_id=row["author_provider_id"],
            status=row["status"],
            findings=findings,
            # asyncpg returns JSONB columns as raw JSON text (no codec
            # registered), so these are parsed back into their Pydantic
            # models here rather than left as bare dicts/lists.
            metrics=AnalysisMetrics.model_validate_json(row["metrics"]),
            rule_statistics=[RuleStatistic.model_validate(s) for s in json.loads(row["rule_statistics"])],
            file_statistics=[FileStatistic.model_validate(s) for s in json.loads(row["file_statistics"])],
            python=PythonAnalysisResult.model_validate_json(row["python_result"]) if row["python_result"] else None,
            changes=(
                PullRequestChanges.model_validate_json(row["pull_request_changes"])
                if row["pull_request_changes"] else None
            ),
            timings=json.loads(row["timings"]) if row["timings"] else {},
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error_message=row["error_message"],
        )

    def _map_finding(self, row: asyncpg.Record) -> Finding:
        return Finding(
            finding_id=row["finding_id"],
            repository=row["repository"],
            pull_request_number=row["pull_request_number"],
            commit_sha=row["commit_sha"],
            file_path=row["file_path"],
            line=row["line"],
            column=row["col"],
            end_line=row["end_line"],
            end_column=row["end_col"],
            severity=row["severity"],
            category=row["category"],
            rule_id=row["rule_id"],
            message=row["message"],
            tool=row["tool"],
            fingerprint=row["fingerprint"],
            remediation_minutes=row["remediation_minutes"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    async def contributor_summary(self, repository: str) -> list[dict]:
        """
        One row per person who has opened an analysed pull request in this
        repository, with what their work amounted to.

        Everything except the analysis count is measured over the *latest*
        analysis of each pull request. A pull request is re-analysed on
        every push, and each run stores the whole diff against the target
        branch, not just what that push added — so summing every run would
        report a pull request pushed to twice as twice the lines, twice
        the files and twice the issues. That is not a rounding error; it
        scales with how often someone pushes.

        The analysis count is deliberately the opposite: it counts every
        run, because "how many times was this reviewed" is a different and
        useful question.

        Aggregated in SQL rather than by loading every result and grouping
        in Python: a busy repository has thousands of analyses, and all
        that is wanted is a handful of totals per person.

        Rows with no author are excluded: they are analyses stored before
        the author was carried through, and showing them as a contributor
        called "unknown" would invite the number to be read as a person.
        """
        rows = await self._pool.fetch(
            """
            WITH scoped AS (
                SELECT *
                FROM analysis_results
                WHERE repository = $1
                  AND author_username IS NOT NULL
                  AND status = 'completed'
            ),
            latest AS (
                SELECT DISTINCT ON (pull_request_number) *
                FROM scoped
                ORDER BY pull_request_number, completed_at DESC NULLS LAST, started_at DESC
            ),
            runs AS (
                SELECT author_username, COUNT(*) AS analyses
                FROM scoped
                GROUP BY author_username
            )
            SELECT
                l.author_username                                   AS username,
                MAX(l.author_provider_id)                           AS provider_user_id,
                COUNT(*)                                            AS pull_requests,
                MAX(r.analyses)                                     AS analyses,
                MAX(l.completed_at)                                 AS last_analysis_at,
                COALESCE(SUM((l.metrics ->> 'total_issues')::int), 0)             AS issues,
                COALESCE(SUM((l.metrics ->> 'errors')::int), 0)                   AS errors,
                COALESCE(SUM((l.metrics ->> 'warnings')::int), 0)                 AS warnings,
                COALESCE(SUM((l.pull_request_changes ->> 'lines_added')::int), 0)   AS lines_added,
                COALESCE(SUM((l.pull_request_changes ->> 'lines_removed')::int), 0) AS lines_removed,
                COALESCE(SUM((l.pull_request_changes ->> 'files_changed')::int), 0) AS files_changed
            FROM latest l
            JOIN runs r ON r.author_username = l.author_username
            GROUP BY l.author_username
            ORDER BY pull_requests DESC, username ASC;
            """,
            repository,
        )
        return [dict(row) for row in rows]

    async def contributor_review_findings(self, repository: str) -> dict[str, dict[str, int]]:
        """
        AI review findings per author, by severity.

        Counted over the latest analysis of each pull request, like the
        summary above: an issue reported on three pushes is one issue,
        not three.

        Separate from contributor_summary because a review lives in its
        own table and its findings are a JSONB array: expanding that array
        multiplies rows, which would corrupt the totals in the query above
        if the two were joined into one.
        """
        rows = await self._pool.fetch(
            """
            WITH latest AS (
                SELECT DISTINCT ON (r.pull_request_number) r.result_id, r.author_username
                FROM analysis_results r
                WHERE r.repository = $1
                  AND r.author_username IS NOT NULL
                  AND r.status = 'completed'
                ORDER BY r.pull_request_number, r.completed_at DESC NULLS LAST, r.started_at DESC
            )
            SELECT
                l.author_username                          AS username,
                finding ->> 'severity'                     AS severity,
                COUNT(*)                                   AS total
            FROM latest l
            JOIN agent_reviews a ON a.result_id = l.result_id
            CROSS JOIN LATERAL jsonb_array_elements(
                COALESCE(a.review -> 'findings', '[]'::jsonb)
            ) AS finding
            WHERE a.status = 'completed'
            GROUP BY l.author_username, finding ->> 'severity';
            """,
            repository,
        )

        by_author: dict[str, dict[str, int]] = {}
        for row in rows:
            by_author.setdefault(row["username"], {})[row["severity"] or "unknown"] = row["total"]
        return by_author

    async def timing_percentiles(self, repository: str | None, days: int) -> dict:
        """
        p50 and p95 for each pipeline stage, over the last `days` days.

        Percentiles rather than averages: the interesting question is
        "how slow is a slow review", and a mean over a few hundred jobs
        hides exactly that. p95 is what someone waiting actually notices.

        Computed in Postgres with percentile_cont over the JSONB keys, so
        adding a stage to the pipeline adds it here with no change: the
        keys are read from the data rather than listed.

        `repository` None aggregates across every repository, which is the
        platform-wide view; a repository narrows it to one.
        """
        rows = await self._pool.fetch(
            """
            WITH scoped AS (
                SELECT timings
                FROM analysis_results
                WHERE started_at >= NOW() - ($2 || ' days')::interval
                  AND timings <> '{}'::jsonb
                  AND ($1::text IS NULL OR repository = $1)
            ),
            unpacked AS (
                SELECT key AS phase, (value #>> '{}')::numeric AS ms
                FROM scoped, jsonb_each(timings)
            )
            SELECT phase,
                   count(*)                                             AS samples,
                   round(percentile_cont(0.5) WITHIN GROUP (ORDER BY ms))  AS p50_ms,
                   round(percentile_cont(0.95) WITHIN GROUP (ORDER BY ms)) AS p95_ms,
                   round(max(ms))                                       AS max_ms
            FROM unpacked
            GROUP BY phase
            ORDER BY p95_ms DESC;
            """,
            repository, str(days),
        )

        return {
            "days": days,
            "repository": repository,
            "phases": [
                {
                    "phase": row["phase"],
                    "samples": row["samples"],
                    "p50_ms": int(row["p50_ms"]),
                    "p95_ms": int(row["p95_ms"]),
                    "max_ms": int(row["max_ms"]),
                }
                for row in rows
            ],
        }
