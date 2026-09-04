from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .finding import Finding
from .metrics import AnalysisMetrics, FileStatistic, RuleStatistic

AnalysisStatus = Literal["completed", "failed"]


class AnalysisResult(BaseModel):
    """
    The outcome of processing one AnalysisJob — one model with a status
    discriminator rather than separate Completed/Failed types, since a
    failed run can still carry partial findings (e.g. the analyzer
    finished before a timeout) that a completed-only model would need
    anyway.

    This is what gets persisted and is the basis for the
    analysis.completed / analysis.failed events this service publishes.
    """

    result_id: UUID = Field(default_factory=uuid4)
    job_id: UUID

    repository: str
    pull_request_number: int
    commit_sha: str
    branch: str

    status: AnalysisStatus
    findings: list[Finding] = Field(default_factory=list)

    # Owned exclusively by this service — computed once in
    # metrics/calculator.py from `findings` plus each analyzed file's line
    # count. Every consumer (main-backend, web-interface) only ever
    # displays these values, never recomputes them.
    metrics: AnalysisMetrics = Field(default_factory=AnalysisMetrics)
    rule_statistics: list[RuleStatistic] = Field(default_factory=list)
    file_statistics: list[FileStatistic] = Field(default_factory=list)

    started_at: datetime
    completed_at: datetime | None = None

    # Populated when status == "failed"; None on a successful run.
    error_message: str | None = None
