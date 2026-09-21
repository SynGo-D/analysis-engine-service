import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from ..agents.reviewer import run_reviewer, validate_review
from ..agents.runtime.provider import LLMProvider
from ..config import settings
from ..context import ContextPackBuilder
from ..domain import AgentReview, AnalysisJob, AnalysisResult, PullRequestChanges, ReviewStats
from ..domain.agent_review import ReviewSkipReason
from ..domain.code_index import RepoIndex
from ..domain import RuleCheck
from ..retrieval import RetrievalTools, ToolExecutor
from ..rules import RuleSet, load_rules
from ..rules.rule_set import RuleSource
from .reporter import rank_and_cap, risk_level
from .verification import verify_findings

logger = logging.getLogger(__name__)


class ReviewOrchestrator:
    """
    Stage 2 for one PR: decide whether to review, build the context pack,
    run the Reviewer, check its claims, rank what survives.

    Always returns an AgentReview and never raises: a skipped or failed
    review is recorded as such (with whatever it cost), and the linter
    result it belongs to is never affected (principle 5).
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        pack_builder: ContextPackBuilder | None = None,
        rule_source: RuleSource | None = None,
    ):
        self._provider = provider
        self._pack_builder = pack_builder or ContextPackBuilder()
        # Rules stored for the repository (dashboard / accepted suggestions).
        # The repository's own rules file is always read, with or without it.
        self._rule_source = rule_source

    def skip_reason(self, changes: PullRequestChanges | None) -> ReviewSkipReason | None:
        if self._provider is None:
            return "disabled"
        if changes is None or changes.change_set is None:
            return "no_diff"
        if changes.unavailable_reason == "too_large":
            return "too_large"
        if changes.status != "available":
            return "no_diff"
        if changes.files_changed > settings.review_max_files or (
            changes.lines_added + changes.lines_removed > settings.review_max_changed_lines
        ):
            return "too_large"
        if not any(f.added_lines or f.deletion_points for f in changes.change_set.files):
            return "no_changes"
        return None

    def pending(self, job: AnalysisJob, result: AnalysisResult) -> AgentReview:
        """The row saved before the review starts, so a client can show "in progress"."""
        return AgentReview(
            result_id=result.result_id, job_id=job.job_id, repository=job.repository,
            pull_request_number=job.pull_request_number, status="running",
            started_at=datetime.now(timezone.utc),
        )

    async def run(
        self,
        workspace: Path,
        job: AnalysisJob,
        result: AnalysisResult,
        index: RepoIndex | None,
        review: AgentReview | None = None,
    ) -> AgentReview:
        review = review or self.pending(job, result)
        started = time.monotonic()

        reason = self.skip_reason(result.changes)
        if reason is None and index is None:
            reason = "no_diff"
        if reason is not None:
            return _finish(review, status="skipped", skip_reason=reason)

        try:
            return await self._review(workspace, job, result, index, review, started)
        except Exception as error:
            logger.exception("[job:%s] AI review failed", job.job_id)
            return _finish(review, status="failed", error=f"{type(error).__name__}: {error}"[:500])

    async def _review(self, workspace, job, result, index, review, started) -> AgentReview:
        change_set = result.changes.change_set
        rules = await load_rules(workspace, job.repository, change_set, self._rule_source)
        review.rule_errors = rules.errors

        pack = await self._pack_builder.build(workspace, job, result.changes, index, result.findings, rules)
        executor = ToolExecutor(RetrievalTools(workspace, index, result.findings, rules=rules))

        run = await run_reviewer(self._provider, pack.render(), executor)

        stats = ReviewStats(
            model=settings.reviewer_model,
            stop_reason=run.stop_reason,
            rounds=run.rounds,
            tool_calls=len(run.tool_calls),
            context_tokens_estimate=pack.estimated_tokens,
            input_tokens=run.usage.input_tokens,
            cached_tokens=run.usage.cached_tokens,
            output_tokens=run.usage.output_tokens,
            reasoning_tokens=run.usage.reasoning_tokens,
            cost_usd=round(run.cost_usd, 6) if run.cost_usd is not None else None,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        review.stats = stats

        if run.output is None:
            return _finish(review, status="failed", error=run.error or f"Reviewer stopped: {run.stop_reason}")

        validated = validate_review(run.output, workspace, result.findings, job.repository, rules)
        surviving = validated.findings
        refuted = []

        if settings.verifier_enabled and surviving:
            verification = await verify_findings(
                self._provider, surviving, workspace=workspace, index=index, linter_findings=result.findings,
                job=job, change_set=change_set, summary=validated.summary, rules=rules,
            )
            surviving, refuted = verification.kept, verification.refuted
            stats.verifier_calls = verification.calls
            stats.verifier_cost_usd = round(verification.cost_usd, 6) if verification.cost_usd is not None else None
            stats.input_tokens += verification.usage.input_tokens
            stats.cached_tokens += verification.usage.cached_tokens
            stats.output_tokens += verification.usage.output_tokens
            stats.reasoning_tokens += verification.usage.reasoning_tokens
            stats.cost_usd = (
                round(stats.cost_usd + verification.cost_usd, 6)
                if stats.cost_usd is not None and verification.cost_usd is not None else None
            )
            stats.duration_ms = int((time.monotonic() - started) * 1000)

        reported = rank_and_cap(surviving)

        stats.candidates_proposed = len(run.output.candidates)
        stats.candidates_dropped = len(validated.dropped)
        stats.candidates_refuted = len(refuted)
        stats.findings_reported = len(reported)

        review.summary = validated.summary
        review.areas_touched = validated.areas_touched
        review.findings = reported
        review.linter_triage = validated.linter_triage
        review.dropped = validated.dropped + refuted
        review.triage_dropped = validated.triage_dropped
        review.rule_checks = _rule_checks(rules, change_set, run.output.rule_checks, reported)
        review.risk_level = risk_level(reported)
        if review.risk_level == "low" and any(c.outcome == "violated" for c in review.rule_checks):
            review.risk_level = "medium"  # a violated rule is never low risk (§10.2)

        logger.info(
            "[job:%s] AI review: %d reported, %d dropped, %d refuted, %d triaged, $%s",
            job.job_id, len(reported), len(validated.dropped), len(refuted), len(validated.linter_triage),
            f"{stats.cost_usd:.5f}" if stats.cost_usd is not None else "unknown",
        )
        return _finish(review, status="completed")


def _rule_checks(rules: RuleSet, change_set, model_checks, reported) -> list[RuleCheck]:
    """
    Each applicable rule's outcome. The Reviewer's word alone never makes a
    rule "violated": only a reported issue citing the rule, which survived
    the evidence checks and the Verifier, does.
    """
    cited = {rule_id for finding in reported for rule_id in finding.rule_ids}
    said = {c.rule_id.strip().strip("[]"): c for c in model_checks}
    relevant = rules.applicable(change_set) + [r for r in rules.rules if r.rule_id in cited]

    checks, seen = [], set()
    for rule in relevant:
        if rule.rule_id in seen:
            continue
        seen.add(rule.rule_id)
        claim = said.get(rule.rule_id)
        if rule.rule_id in cited:
            outcome = "violated"
        elif claim is None:
            outcome = "not_checked"
        elif claim.outcome == "violated":
            outcome = "not_confirmed"
        else:
            outcome = claim.outcome
        checks.append(RuleCheck(rule_id=rule.rule_id, rule=rule.rule, severity=rule.severity, outcome=outcome,
                                note=claim.note if claim else None))
    return checks


def _finish(review: AgentReview, *, status, skip_reason=None, error=None) -> AgentReview:
    review.status = status
    review.skip_reason = skip_reason
    review.error_message = error
    review.completed_at = datetime.now(timezone.utc)
    return review


def build_default_provider() -> LLMProvider | None:
    """The configured provider, or None when AI review is unavailable (no key, or disabled)."""
    if not settings.agent_review_available:
        return None
    from ..agents.runtime.openai_provider import OpenAIProvider

    return OpenAIProvider(api_key=settings.openai_api_key.get_secret_value())
