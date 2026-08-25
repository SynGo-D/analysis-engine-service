import asyncpg

from ..domain import AnalysisResult, Finding


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

    async def save(self, result: AnalysisResult) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO analysis_results (
                        result_id, job_id, repository, pull_request_number,
                        commit_sha, status, error_message, started_at, completed_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (result_id) DO NOTHING;
                    """,
                    result.result_id,
                    result.job_id,
                    result.repository,
                    result.pull_request_number,
                    result.commit_sha,
                    result.status,
                    result.error_message,
                    result.started_at,
                    result.completed_at,
                )

                if result.findings:
                    await conn.executemany(
                        """
                        INSERT INTO findings (
                            finding_id, result_id, repository, pull_request_number,
                            commit_sha, file_path, line, col, severity, category,
                            rule_id, message, tool, fingerprint, remediation_minutes
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15);
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
                                finding.severity,
                                finding.category,
                                finding.rule_id,
                                finding.message,
                                finding.tool,
                                finding.fingerprint,
                                finding.remediation_minutes,
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
        return self._map_result(result_row, findings=findings)

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
            status=row["status"],
            findings=findings,
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
            severity=row["severity"],
            category=row["category"],
            rule_id=row["rule_id"],
            message=row["message"],
            tool=row["tool"],
            fingerprint=row["fingerprint"],
            remediation_minutes=row["remediation_minutes"],
        )
