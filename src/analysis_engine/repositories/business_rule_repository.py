import json

import asyncpg

from ..domain.business_rule import BusinessRule, RuleStatus


class BusinessRuleRepository:
    """
    Rules stored per repository: added in the dashboard, or suggested by the
    Rule Miner and then accepted or rejected. Rules from a repository's own
    `.codepulse/rules.yml` are never stored here; they're read from the
    target branch at review time.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def active_rules(self, repository: str) -> list[BusinessRule]:
        return await self.list_rules(repository, status="active")

    async def list_rules(self, repository: str, status: RuleStatus | None = None) -> list[BusinessRule]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM business_rules
                WHERE repository = $1 AND ($2::text IS NULL OR status = $2)
                ORDER BY status, rule_id;
                """,
                repository, status,
            )
        return [self._map(row) for row in rows]

    async def get(self, repository: str, rule_id: str) -> BusinessRule | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM business_rules WHERE repository = $1 AND rule_id = $2;", repository, rule_id
            )
        return self._map(row) if row else None

    async def save(self, repository: str, rule: BusinessRule) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO business_rules (repository, rule_id, rule, applies_to, severity, rationale, source, status, evidence)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (repository, rule_id) DO UPDATE SET
                    rule = EXCLUDED.rule, applies_to = EXCLUDED.applies_to, severity = EXCLUDED.severity,
                    rationale = EXCLUDED.rationale, source = EXCLUDED.source, status = EXCLUDED.status,
                    evidence = EXCLUDED.evidence, updated_at = NOW();
                """,
                repository, rule.rule_id, rule.rule, json.dumps(rule.applies_to), rule.severity, rule.rationale,
                rule.source, rule.status, rule.evidence,
            )

    async def insert_suggestions(self, repository: str, rules: list[BusinessRule]) -> int:
        """Stores suggestions, never overwriting a rule that already exists (a person may have edited it)."""
        inserted = 0
        async with self._pool.acquire() as conn:
            for rule in rules:
                result = await conn.execute(
                    """
                    INSERT INTO business_rules (repository, rule_id, rule, applies_to, severity, rationale, source, status, evidence)
                    VALUES ($1, $2, $3, $4, $5, $6, 'suggested', 'suggested', $7)
                    ON CONFLICT (repository, rule_id) DO NOTHING;
                    """,
                    repository, rule.rule_id, rule.rule, json.dumps(rule.applies_to), rule.severity,
                    rule.rationale, rule.evidence,
                )
                inserted += result.endswith(" 1")
        return inserted

    async def delete(self, repository: str, rule_id: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM business_rules WHERE repository = $1 AND rule_id = $2;", repository, rule_id
            )
        return result.endswith(" 1")

    def _map(self, row: asyncpg.Record) -> BusinessRule:
        return BusinessRule(
            rule_id=row["rule_id"], rule=row["rule"], applies_to=json.loads(row["applies_to"]),
            severity=row["severity"], rationale=row["rationale"], source=row["source"], status=row["status"],
            evidence=row["evidence"],
        )
