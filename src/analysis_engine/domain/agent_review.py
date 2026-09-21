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
    type: Literal["code_location", "linter_finding", "call_path", "test"]
    ref: str
    quote: str | None = None
    # True when the engine checked it against the repository (the quote is
    # really at that location, the finding really exists). call_path
    # evidence can't be checked mechanically yet, so it stays False.
    verified: bool = False


class AgentFinding(BaseModel):
    """An issue the AI review reports, with the evidence it rests on."""

    finding_id: UUID = Field(default_factory=uuid4)
    # Stable across pushes to the same PR, so feedback carries over (§10.1).
    fingerprint: str
    title: str
    category: Literal["correctness", "security"]
    severity: Severity
    confidence: float
    file_path: str
    line_start: int
    line_end: int
    explanation: str
    evidence: list[ReviewEvidence]
    suggested_fix: str | None = None
    # Phase 4 adds the Verifier; until then every reported issue has only
    # passed the engine's mechanical evidence checks.
    verification: Literal["unverified", "verified"] = "unverified"
    source: str = "reviewer"


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


class DroppedCandidate(BaseModel):
    """An issue the Reviewer proposed that the engine discarded — kept for evaluation, never shown as a finding."""

    title: str
    reason: str


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
    candidates_dropped: int = 0
    findings_reported: int = 0


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

    stats: ReviewStats | None = None
    error_message: str | None = None

    started_at: datetime
    completed_at: datetime | None = None
