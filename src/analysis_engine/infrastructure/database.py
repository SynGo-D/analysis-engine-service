import asyncpg

from ..config import settings


async def connect_database() -> asyncpg.Pool:
    """
    Creates the shared Postgres connection pool.

    Fail-fast: raises if the database is unreachable at startup — the
    Python equivalent of the Node services' connectDatabase(), which calls
    process.exit(1) directly. Here, the exception propagates up through
    the FastAPI lifespan context manager instead; uvicorn treats a
    lifespan-startup exception as fatal and exits non-zero on its own,
    which is the idiomatic way to fail a FastAPI service's startup rather
    than calling sys.exit() from inside a connection helper.
    """
    try:
        pool = await asyncpg.create_pool(
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
            min_size=1,
            max_size=10,
        )
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1;")

        print("Analysis Engine connected to PostgreSQL.")
        return pool

    except Exception as error:
        print(f"Failed to connect to PostgreSQL: {error}")
        raise
