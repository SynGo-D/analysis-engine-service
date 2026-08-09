from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .finding import Finding

AnalysisStatus = Literal["completed", "failed"]


class SeverityCounts(BaseModel):
    error: int = 0
    warning: int = 0
    info: int = 0


class TechnicalDebtSummary(BaseModel):
    """
    Inputs for SQALE-based technical-debt calculation — the raw
    ingredients (computed in Phase 8 by aggregating over `findings`), not
    a final debt-ratio/rating. Deliberately not coupled to any specific
    downstream rating formula: that belongs to a Technical Debt/Quality
    service, per the spec's explicit instruction not to tie this engine to
    the final dashboard representation.
    """

    total_remediation_minutes: int = 0
    issue_count_by_severity: SeverityCounts = Field(default_factory=SeverityCounts)
    issue_count_by_category: dict[str, int] = Field(default_factory=dict)
    affected_files: int = 0


class AnalysisResult(BaseModel):
    """
    The outcome of processing one AnalysisJob — one model with a status
    discriminator rather than separate Completed/Failed types, since a
    failed run can still carry partial findings (e.g. some tools finished
    before a timeout) that a completed-only model would need anyway.

    This is what gets persisted (Phase 9) and is the basis for the
    analysis.completed / analysis.failed events published in Phase 10.
    """

    result_id: UUID = Field(default_factory=uuid4)
    job_id: UUID

    repository: str
    pull_request_number: int
    commit_sha: str

    status: AnalysisStatus
    findings: list[Finding] = Field(default_factory=list)
    technical_debt: TechnicalDebtSummary = Field(default_factory=TechnicalDebtSummary)

    started_at: datetime
    completed_at: datetime | None = None

    # Populated when status == "failed"; None on a successful run.
    error_message: str | None = None
