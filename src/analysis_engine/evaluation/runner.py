import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..agents.runtime.provider import Completion, CompletionRequest, LLMProvider, ToolCall, Usage
from ..application.orchestrator import AnalysisOrchestrator
from ..config import settings
from ..review import ReviewOrchestrator
from .case import Case
from .fixture_repo import CaseCheckout
from .scoring import CaseScore, Summary, score_case, summarise

logger = logging.getLogger(__name__)

# Targets from docs/agent-architecture.md §12.
TARGETS = {"precision": 0.70, "recall": 0.50, "clean_false_alarm_rate": 0.10, "p95_duration_s": 90.0,
           "cost_per_pr_usd": 0.30}

RESULTS_DIR = Path(__file__).resolve().parents[3] / "evaluation-results"


class DryRunProvider:
    """
    Answers every review with an empty submission and zero usage. Runs the
    whole pipeline — repositories, diff, linters, context pack, validation,
    scoring — without calling a model, to check a change to the harness or
    the cases before spending anything.
    """

    async def complete(self, request: CompletionRequest) -> Completion:
        empty = '{"summary": "dry run", "areas_touched": [], "linter_triage": [], "candidates": []}'
        return Completion(tool_calls=[ToolCall("dry", "submit_review", empty)], text="", usage=Usage())

    def user_message(self, text: str):
        return {"role": "user", "content": text}

    def tool_result(self, call_id: str, output: str):
        return {"type": "function_call_output", "call_id": call_id, "output": output}


async def run_case(case: Case, provider: LLMProvider, sinks=None) -> tuple[CaseScore, int]:
    """`sinks`: optional (on_result, on_review) to store the run, e.g. for viewing it in the dashboard."""
    checkout = await asyncio.to_thread(CaseCheckout, case)
    try:
        orchestrator = AnalysisOrchestrator(workspace_manager=checkout,
                                            review_orchestrator=ReviewOrchestrator(provider))
        on_result, on_review = sinks or (None, None)
        result = await orchestrator.run(checkout.job(), on_result=on_result, on_review=on_review)
        review = result.review
        context_tokens = review.stats.context_tokens_estimate if review and review.stats else 0
        return score_case(case, review), context_tokens
    finally:
        checkout.cleanup()


async def run_evaluation(
    cases: tuple[Case, ...],
    provider: LLMProvider,
    concurrency: int = 4,
    max_total_cost_usd: float = 0.25,
    sinks=None,
) -> tuple[list[CaseScore], dict[str, int]]:
    """
    Runs every case, a few at a time. Once the money spent reaches
    `max_total_cost_usd`, no further case is started — a cap on the whole
    run, on top of each review's own cap.
    """
    semaphore = asyncio.Semaphore(concurrency)
    spent = 0.0
    scores: dict[str, CaseScore] = {}
    context_tokens: dict[str, int] = {}

    async def one(case: Case) -> None:
        nonlocal spent
        async with semaphore:
            if spent >= max_total_cost_usd:
                scores[case.id] = CaseScore(case_id=case.id, clean=case.is_clean, status="not_run",
                                            expected=len(case.expected), error="evaluation cost cap reached")
                return
            score, tokens = await run_case(case, provider, sinks)
            spent += score.cost_usd
            scores[case.id] = score
            context_tokens[case.id] = tokens
            logger.warning("%-30s %-10s found %d/%d, false alarms %d, $%.5f",
                           case.id, score.status, score.found, score.expected, score.false_positives, score.cost_usd)

    await asyncio.gather(*(one(case) for case in cases))
    return [scores[case.id] for case in cases], context_tokens


def report(scores: list[CaseScore], summary: Summary, model: str, dry_run: bool) -> str:
    lines = [f"\nEvaluation: {summary.cases} PRs ({summary.buggy_cases} with a planted bug, "
             f"{summary.clean_cases} clean) · {model}{' · DRY RUN (no model called)' if dry_run else ''}", ""]

    for s in scores:
        verdict = "clean" if s.clean else f"found {s.found}/{s.expected}"
        lines.append(f"  {s.case_id:32} {verdict:12} false alarms {s.false_positives}  "
                     f"dropped {s.dropped_by_validation}  ${s.cost_usd:.5f}  {s.duration_ms / 1000:5.1f}s"
                     + (f"  [{s.status}: {s.error}]" if s.error else ""))
        for missed in s.missed:
            lines.append(f"      missed: {missed}")
        for extra in s.unexpected:
            lines.append(f"      unexpected: {extra}")
        for reason in s.drop_reasons:
            lines.append(f"      dropped by {reason}")

    def metric(name: str, value: float | None, fmt: str, better: str) -> str:
        if value is None:
            return f"  {name:26} n/a"
        target = TARGETS[name]
        ok = value >= target if better == "higher" else value <= target
        return f"  {name:26} {value:{fmt}}   target {'≥' if better == 'higher' else '≤'} {target:{fmt}}  {'✓' if ok else '✗'}"

    lines += [
        "",
        metric("precision", summary.precision, ".2f", "higher"),
        metric("recall", summary.recall, ".2f", "higher"),
        metric("clean_false_alarm_rate", summary.clean_false_alarm_rate, ".2f", "lower"),
        metric("p95_duration_s", summary.p95_duration_s, ".1f", "lower"),
        metric("cost_per_pr_usd", summary.cost_per_pr_usd, ".5f", "lower"),
        f"  {'failed reviews':26} {summary.failed_reviews}",
        f"  {'dropped by evidence checks':26} {summary.dropped_by_validation}",
        f"  {'total cost':26} ${summary.total_cost_usd:.5f}",
    ]
    return "\n".join(lines)


def save(scores: list[CaseScore], summary: Summary, model: str, context_tokens: dict[str, int]) -> Path:
    """Stores the run so later runs (a new prompt, a new model) can be compared against it."""
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{stamp}-{model}.json"
    path.write_text(json.dumps({
        "model": model,
        "reasoning_effort": settings.reviewer_reasoning_effort,
        "verifier": settings.verifier_enabled,
        "case_ids": sorted(s.case_id for s in scores),
        "summary": asdict(summary),
        "cases": [asdict(s) | {"context_tokens_estimate": context_tokens.get(s.case_id, 0)} for s in scores],
    }, indent=2))
    return path


def previous_summary(model: str, case_ids: list[str], exclude: Path | None = None) -> dict | None:
    """The latest earlier run of the same model on exactly the same cases — anything else isn't comparable."""
    if not RESULTS_DIR.exists():
        return None
    for path in sorted((p for p in RESULTS_DIR.glob(f"*-{model}.json") if p != exclude), reverse=True):
        run = json.loads(path.read_text())
        if run.get("case_ids") == sorted(case_ids):
            return run["summary"] | {"verifier": run.get("verifier")}
    return None
