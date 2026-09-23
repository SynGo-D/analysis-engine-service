"""
Scores the AI review against pull requests with planted bugs.

    .venv/bin/python -m analysis_engine.evaluation --dry-run   # free: checks everything but the model
    .venv/bin/python -m analysis_engine.evaluation             # real run, capped at --max-cost

A full run on gpt-5.6-luna costs about two cents.
"""

import argparse
import asyncio
import logging
import sys

from ..config import settings
from ..review import build_default_provider
from .cases import ALL_CASES
from .runner import DryRunProvider, previous_summary, report, run_evaluation, save
from .scoring import summarise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="run everything except the model (free)")
    parser.add_argument("--model", help=f"override REVIEWER_MODEL (default {settings.reviewer_model})")
    parser.add_argument("--cases", help="comma-separated case ids or id prefixes, e.g. 'py-,clean-rename'")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-cost", type=float, default=0.25, help="stop starting cases after this many USD")
    parser.add_argument("--no-verifier", action="store_true", help="report the Reviewer's issues without verification")
    parser.add_argument("--save", action="store_true",
                        help="store each case's result and review in the database, viewable in the dashboard at "
                             "/developer/analysis/eval/<case-id>/1")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if args.model:
        settings.reviewer_model = args.model
    if args.no_verifier:
        settings.verifier_enabled = False

    cases = ALL_CASES
    if args.cases:
        wanted = [w.strip() for w in args.cases.split(",") if w.strip()]
        cases = tuple(c for c in ALL_CASES if any(c.id == w or c.id.startswith(w) for w in wanted))
        if not cases:
            sys.exit(f"No cases match {args.cases!r}")

    provider = DryRunProvider() if args.dry_run else build_default_provider()
    if provider is None:
        sys.exit("No OPENAI_API_KEY configured. Use --dry-run, or set the key in .env.")

    scores, context_tokens = asyncio.run(_run(cases, provider, args))
    summary = summarise(scores)
    print(report(scores, summary, settings.reviewer_model, args.dry_run))

    if not args.dry_run:
        path = save(scores, summary, settings.reviewer_model, context_tokens)
        previous = previous_summary(settings.reviewer_model, [s.case_id for s in scores], exclude=path)
        if previous:
            print(f"\n  previous run on these cases (verifier {'on' if previous['verifier'] else 'off'}): "
                  f"precision {previous['precision']}, recall {previous['recall']}, "
                  f"clean false alarms {previous['clean_false_alarm_rate']}, cost ${previous['total_cost_usd']:.5f}")
        print(f"\n  saved to {path}")
    else:
        tokens = sorted(context_tokens.values())
        if tokens:
            print(f"\n  context packs: {min(tokens)}–{max(tokens)} tokens (estimate)")


async def _run(cases, provider, args):
    if not args.save:
        return await run_evaluation(cases, provider, concurrency=args.concurrency, max_total_cost_usd=args.max_cost)

    import asyncpg

    from ..infrastructure.schema import ensure_schema
    from ..repositories.agent_review_repository import AgentReviewRepository
    from ..repositories.analysis_result_repository import AnalysisResultRepository

    pool = await asyncpg.create_pool(host=settings.db_host, port=settings.db_port, database=settings.db_name,
                                     user=settings.db_user, password=settings.db_password)
    try:
        await ensure_schema(pool)
        sinks = (AnalysisResultRepository(pool).save, AgentReviewRepository(pool).save)
        return await run_evaluation(cases, provider, concurrency=args.concurrency,
                                    max_total_cost_usd=args.max_cost, sinks=sinks)
    finally:
        await pool.close()


if __name__ == "__main__":
    main()
