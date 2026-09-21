from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

ReviewStatus = Literal["pending", "running", "completed", "failed", "skipped"]

ReviewSkipReason = Literal[
    "disabled",       # no API key, or AGENT_REVIEW_ENABLED=false
    "no_diff",        # the PR's changes couldn't be worked out
    "too_large",      # over review_max_files / review_max_changed_lines
    "no_changes",     # nothing reviewable changed (only generated or binary files)
]

Severity = Literal["high", "medium", "low"]
RiskLevel = Literal["low", "medium", "high"]


class ReviewEvidence(BaseModel):
    type: Literal["code_location", "linter_finding", "business_rule", "call_path", "test"]
    ref: str
    quote: str | None = None
    # True when the engine checked it against the repository (the quote is
    # really at that location, the finding really exists). call_path
    # evidence can't be checked mechanically yet, so it stays False.
    verified: bool = False


FeedbackVerdict = Literal["useful", "not_useful", "wrong"]


class FeedbackSummary(BaseModel):
    """What developers said about one issue (attached when a review is read)."""

    useful: int = 0
    not_useful: int = 0
    wrong: int = 0
    # The requesting user's own verdict, so the dashboard can show it.
    mine: FeedbackVerdict | None = None


class AgentFinding(BaseModel):
    """An issue the AI review reports, with the evidence it rests on."""

    finding_id: UUID = Field(default_factory=uuid4)
    # Stable across pushes to the same PR, so feedback carries over (§10.1).
    fingerprint: str
    title: str
    category: Literal["correctness", "security", "business_rule"]
    severity: Severity
    confidence: float
    file_path: str
    line_start: int
    line_end: int
    explanation: str
    evidence: list[ReviewEvidence]
    suggested_fix: str | None = None
    # Business rules this issue cites (their ids), when it's a rule violation.
    rule_ids: list[str] = Field(default_factory=list)
    # Phase 4 adds the Verifier; until then every reported issue has only
    # passed the engine's mechanical evidence checks.
    verification: Literal["unverified", "verified"] = "unverified"
    source: str = "reviewer"
    # Filled in when the review is read; never stored with the review.
    feedback: FeedbackSummary | None = None


class TriagedLinterFinding(BaseModel):
    """A linter finding the Reviewer judged relevant to this PR, and why."""

    fingerprint: str
    tool: str
    rule_id: str
    file_path: str
    line: int | None
    message: str
    importance: Severity
    reason: str


class RuleCheck(BaseModel):
    """How the PR stands against one business rule."""

    rule_id: str
    rule: str
    severity: Severity
    # "violated" only when a reported issue that cites the rule survived
    # every check. "not_confirmed": the Reviewer said violated, but no such
    # issue survived. "not_checked": the rule applied and the Reviewer gave
    # no verdict.
    outcome: Literal["violated", "satisfied", "not_applicable", "not_confirmed", "not_checked"]
    note: str | None = None


class DroppedCandidate(BaseModel):
    """An issue the Reviewer proposed that the engine discarded — kept for evaluation, never shown as a finding."""

    title: str
    reason: str
    # "evidence": failed the mechanical checks. "verifier": refuted by Agent 2.
    # "feedback": a developer marked this same issue wrong on an earlier review.
    stage: Literal["evidence", "verifier", "feedback"] = "evidence"


class DroppedTriage(BaseModel):
    """A linter-triage entry the engine discarded because its reference matched no finding."""

    ref: str
    reason: str


class ReviewStats(BaseModel):
    model: str
    stop_reason: str
    rounds: int = 0
    tool_calls: int = 0
    context_tokens_estimate: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None
    duration_ms: int = 0
    candidates_proposed: int = 0
    candidates_dropped: int = 0          # by the evidence checks
    candidates_refuted: int = 0          # by the Verifier
    findings_reported: int = 0
    # cost_usd above is the whole review; this is the Verifier's share.
    verifier_calls: int = 0
    verifier_cost_usd: float | None = None


class AgentReview(BaseModel):
    """The AI review of one analysis result (docs/agent-architecture.md §10.1)."""

    review_id: UUID = Field(default_factory=uuid4)
    result_id: UUID
    job_id: UUID
    repository: str
    pull_request_number: int

    status: ReviewStatus
    skip_reason: ReviewSkipReason | None = None

    summary: str | None = None
    areas_touched: list[str] = Field(default_factory=list)
    risk_level: RiskLevel | None = None
    findings: list[AgentFinding] = Field(default_factory=list)
    linter_triage: list[TriagedLinterFinding] = Field(default_factory=list)
    dropped: list[DroppedCandidate] = Field(default_factory=list)
    triage_dropped: list[DroppedTriage] = Field(default_factory=list)
    rule_checks: list[RuleCheck] = Field(default_factory=list)
    # Problems with the repository's rules file, e.g. a malformed rule.
    rule_errors: list[str] = Field(default_factory=list)

    stats: ReviewStats | None = None
    error_message: str | None = None

    started_at: datetime
    completed_at: datetime | None = None
