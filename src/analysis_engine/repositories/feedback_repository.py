from datetime import datetime, timedelta, timezone

import asyncpg

from ..domain import FeedbackSummary, FeedbackVerdict


class FeedbackRepository:
    """Developer verdicts on AI review issues, and the per-repository usage report."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record(self, repository: str, fingerprint: str, user_id: str, verdict: FeedbackVerdict,
                     note: str | None, pull_request_number: int | None) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_finding_feedback (repository, fingerprint, user_id, verdict, note, pull_request_number)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (repository, fingerprint, user_id) DO UPDATE SET
                    verdict = EXCLUDED.verdict, note = EXCLUDED.note, updated_at = NOW();
                """,
                repository, fingerprint, user_id, verdict, note, pull_request_number,
            )

    async def summaries(self, repository: str, fingerprints: list[str],
                        user_id: str | None = None) -> dict[str, FeedbackSummary]:
        if not fingerprints:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT fingerprint, verdict, user_id FROM agent_finding_feedback
                WHERE repository = $1 AND fingerprint = ANY($2::text[]);
                """,
                repository, fingerprints,
            )
        result: dict[str, FeedbackSummary] = {}
        for row in rows:
            summary = result.setdefault(row["fingerprint"], FeedbackSummary())
            setattr(summary, row["verdict"], getattr(summary, row["verdict"]) + 1)
            if user_id and row["user_id"] == user_id:
                summary.mine = row["verdict"]
        return result

    async def wrong_fingerprints(self, repository: str) -> set[str]:
        """Issues a developer has marked wrong: later reviews don't report them again."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT fingerprint FROM agent_finding_feedback WHERE repository = $1 AND verdict = 'wrong';",
                repository,
            )
        return {row["fingerprint"] for row in rows}

    async def usage(self, repository: str, days: int) -> dict:
        """Reviews, cost and developer feedback for one repository over the last `days` days."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        async with self._pool.acquire() as conn:
            reviews = await conn.fetchrow(
                """
                SELECT count(*) AS reviews,
                       count(*) FILTER (WHERE status = 'completed') AS completed,
                       count(*) FILTER (WHERE status = 'failed') AS failed,
                       count(*) FILTER (WHERE status = 'skipped') AS skipped,
                       coalesce(sum(cost_usd), 0) AS cost_usd,
                       coalesce(sum(jsonb_array_length(review -> 'findings')), 0) AS issues_reported
                FROM agent_reviews WHERE repository = $1 AND created_at >= $2;
                """,
                repository, since,
            )
            feedback = await conn.fetch(
                """
                SELECT verdict, count(*) AS n FROM agent_finding_feedback
                WHERE repository = $1 AND updated_at >= $2 GROUP BY verdict;
                """,
                repository, since,
            )
        counts = {"useful": 0, "not_useful": 0, "wrong": 0} | {row["verdict"]: row["n"] for row in feedback}
        rated = sum(counts.values())
        completed = reviews["completed"]
        cost = float(reviews["cost_usd"])
        return {
            "repository": repository,
            "days": days,
            "reviews": reviews["reviews"],
            "completed": completed,
            "failed": reviews["failed"],
            "skipped": reviews["skipped"],
            "cost_usd": round(cost, 6),
            "cost_per_review_usd": round(cost / completed, 6) if completed else None,
            "issues_reported": reviews["issues_reported"],
            "feedback": counts,
            # The share of rated issues developers called wrong: the real
            # false-alarm rate, as opposed to the evaluation harness's estimate.
            "wrong_rate": round(counts["wrong"] / rated, 3) if rated else None,
            "useful_rate": round(counts["useful"] / rated, 3) if rated else None,
        }
