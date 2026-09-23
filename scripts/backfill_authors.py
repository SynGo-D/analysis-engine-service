"""
Attributes analyses that were stored before the author was carried through.

Until the author reached the queue, every analysis was anonymous. Those
rows are not wrong, just unattributed, and without this the contributor
page would start empty and slowly fill as new pull requests arrive.

One provider call lists a repository's pull requests with their authors;
the rest is a local update keyed on pull request number. Safe to re-run:
it only touches rows whose author is still NULL.

    python scripts/backfill_authors.py owner/repo [--commit]

Without --commit it reports what it would change and writes nothing.
"""
import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from analysis_engine.config import settings  # noqa: E402
from analysis_engine.workspace import default_credentials  # noqa: E402

GITHUB_API = "https://api.github.com"
PER_PAGE = 100


async def pull_request_authors(repository: str, token: str | None) -> dict[int, tuple[str, str]]:
    """PR number -> (username, provider user id), for every pull request."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "codepulse-backfill"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    authors: dict[int, tuple[str, str]] = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        page = 1
        while True:
            response = await client.get(
                f"{GITHUB_API}/repos/{repository}/pulls",
                params={"state": "all", "per_page": PER_PAGE, "page": page},
                headers=headers,
            )
            if response.status_code != 200:
                raise SystemExit(f"GitHub answered {response.status_code}: {response.text[:200]}")

            batch = response.json()
            if not batch:
                break

            for pull_request in batch:
                user = pull_request.get("user")
                if user:
                    authors[pull_request["number"]] = (user["login"], str(user["id"]))

            if len(batch) < PER_PAGE:
                break
            page += 1

    return authors


async def main(repository: str, commit: bool) -> None:
    pool = await asyncpg.create_pool(
        host=settings.db_host, port=settings.db_port, database=settings.db_name,
        user=settings.db_user, password=settings.db_password,
    )
    try:
        unattributed = await pool.fetch(
            """
            SELECT DISTINCT pull_request_number
            FROM analysis_results
            WHERE repository = $1 AND author_username IS NULL
            ORDER BY pull_request_number;
            """,
            repository,
        )
        numbers = [row["pull_request_number"] for row in unattributed]

        if not numbers:
            print(f"{repository}: every analysis already has an author.")
            return

        print(f"{repository}: {len(numbers)} pull request(s) without an author: {numbers}")

        credentials = default_credentials()
        token = await credentials.token_for("github", repository) if credentials else None
        print("using", "the repository's stored token" if token else "anonymous access")

        authors = await pull_request_authors(repository, token)

        matched = {number: authors[number] for number in numbers if number in authors}
        missing = [number for number in numbers if number not in authors]

        for number, (username, provider_id) in sorted(matched.items()):
            print(f"  PR #{number} -> {username} ({provider_id})")
        for number in missing:
            print(f"  PR #{number} -> no author reported; left unattributed")

        if not commit:
            print(f"\nDry run. Re-run with --commit to attribute {len(matched)} pull request(s).")
            return

        async with pool.acquire() as conn:
            async with conn.transaction():
                for number, (username, provider_id) in matched.items():
                    await conn.execute(
                        """
                        UPDATE analysis_results
                        SET author_username = $3, author_provider_id = $4
                        WHERE repository = $1 AND pull_request_number = $2
                          AND author_username IS NULL;
                        """,
                        repository, number, username, provider_id,
                    )

        print(f"\nAttributed {len(matched)} pull request(s).")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="owner/repo")
    parser.add_argument("--commit", action="store_true", help="write the changes")
    args = parser.parse_args()

    asyncio.run(main(args.repository, args.commit))
