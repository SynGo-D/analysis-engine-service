from uuid import UUID

import asyncpg

from ..domain import AgentReview


class AgentReviewRepository:
    """
    Data access for `agent_reviews`. A review is saved twice — once as
    "running" when it starts, then with its outcome — so saving is an
    upsert on the result it belongs to.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save(self, review: AgentReview) -> None:
        cost = review.stats.cost_usd if review.stats else None
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_reviews (review_id, result_id, repository, pull_request_number, status, cost_usd, review)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (result_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    cost_usd = EXCLUDED.cost_usd,
                    review = EXCLUDED.review,
                    updated_at = NOW();
                """,
                review.review_id, review.result_id, review.repository, review.pull_request_number,
                review.status, cost, review.model_dump_json(),
            )

    async def get_for_results(self, result_ids: list[UUID]) -> dict[UUID, AgentReview]:
        if not result_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT result_id, review FROM agent_reviews WHERE result_id = ANY($1::uuid[]);", result_ids
            )
        return {row["result_id"]: AgentReview.model_validate_json(row["review"]) for row in rows}
