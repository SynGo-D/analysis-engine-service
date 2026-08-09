# repositories

Data-access layer (Repository Pattern) — direct database drivers only
(`asyncpg` for Postgres), no ORM, matching the platform's existing
convention. Persists analysis job metadata and results.

Whether MongoDB is also needed for large/flexible result documents is a
decision for this phase specifically, not assumed upfront — see Phase 9.

Planned: Phase 9.
