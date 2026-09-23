"""
Runs the whole pipeline — linters, PR changes, AI review — on a real pull
request branch, and prints the review and exactly what it cost.

This CALLS THE MODEL and spends money (a few tenths of a cent on the
default model; hard-capped by REVIEW_MAX_COST_USD). Use
scripts/preview_context_pack.py to inspect the input for free first.

    .venv/bin/python scripts/review_pr.py \\
        https://github.com/SynGo-D/test-project.git "branch#1" main --pr 1 --save

--save stores the result and review in the database, so the dashboard
shows them for that PR number.
"""

import argparse
import asyncio
import json
import logging
import subprocess
import sys

import asyncpg

from analysis_engine.application.orchestrator import AnalysisOrchestrator
from analysis_engine.config import settings
from analysis_engine.domain import AnalysisJob
from analysis_engine.infrastructure.schema import ensure_schema
from analysis_engine.repositories.agent_review_repository import AgentReviewRepository
from analysis_engine.repositories.analysis_result_repository import AnalysisResultRepository
from analysis_engine.review import ReviewOrchestrator, build_default_provider


def _head_of(clone_url: str, branch: str) -> str:
    output = subprocess.run(["git", "ls-remote", clone_url, f"refs/heads/{branch}"],
                            check=True, capture_output=True, text=True).stdout
    if not output:
        sys.exit(f"Branch '{branch}' not found at {clone_url}")
    return output.split()[0]


async def main(args) -> None:
    provider = build_default_provider()
    if provider is None:
        sys.exit("AI review is unavailable: set OPENAI_API_KEY in .env (and AGENT_REVIEW_ENABLED=true).")

    repository = args.clone_url.removeprefix("https://").split("/", 1)[1].removesuffix(".git")
    job = AnalysisJob(
        provider="github", repository=repository, clone_url=args.clone_url,
        commit_sha=_head_of(args.clone_url, args.branch), branch=args.branch,
        pull_request_number=args.pr, queued_at="manual", target_branch=args.target_branch,
        title=args.title, description=args.description,
    )

    pool = None
    on_result = on_review = None
    if args.save:
        pool = await asyncpg.create_pool(host=settings.db_host, port=settings.db_port, database=settings.db_name,
                                         user=settings.db_user, password=settings.db_password)
        await ensure_schema(pool)
        on_result = AnalysisResultRepository(pool).save
        on_review = AgentReviewRepository(pool).save

    orchestrator = AnalysisOrchestrator(review_orchestrator=ReviewOrchestrator(provider))
    result = await orchestrator.run(job, on_result=on_result, on_review=on_review)
    if pool:
        await pool.close()

    review = result.review
    print(json.dumps(review.model_dump(mode="json", exclude={"dropped"} if not args.show_dropped else None), indent=2))
    stats = review.stats
    if stats:
        cost = f"${stats.cost_usd:.5f}" if stats.cost_usd is not None else "unknown"
        print(f"\n--- {review.status} ({stats.stop_reason}) with {settings.reviewer_model}: "
              f"{stats.rounds} round(s), {stats.tool_calls} tool call(s), "
              f"{stats.input_tokens:,} in ({stats.cached_tokens:,} cached) / {stats.output_tokens:,} out "
              f"({stats.reasoning_tokens:,} reasoning), {stats.duration_ms / 1000:.1f}s, cost {cost}", file=sys.stderr)
        print(f"    {stats.candidates_proposed} candidate(s), {stats.candidates_dropped} dropped by evidence checks, "
              f"{stats.findings_reported} reported, {len(review.linter_triage)} linter finding(s) triaged",
              file=sys.stderr)
    elif review.error_message or review.skip_reason:
        print(f"\n--- {review.status}: {review.skip_reason or review.error_message}", file=sys.stderr)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clone_url")
    parser.add_argument("branch")
    parser.add_argument("target_branch")
    parser.add_argument("--pr", type=int, default=0, help="PR number to record the result under")
    parser.add_argument("--title")
    parser.add_argument("--description")
    parser.add_argument("--save", action="store_true", help="store the result and review in the database")
    parser.add_argument("--show-dropped", action="store_true", help="also print candidates the evidence checks discarded")
    asyncio.run(main(parser.parse_args()))
