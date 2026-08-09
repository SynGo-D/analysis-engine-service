from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

Severity = Literal["error", "warning", "info"]

FindingCategory = Literal[
    "bug",
    "vulnerability",
    "code_smell",
    "style",
    "complexity",
    "maintainability",
]


class Finding(BaseModel):
    """
    A single normalized issue — the common shape every tool's raw output
    gets translated into (Phase 6/7), so the Dashboard/Technical-Debt
    services never need to know which tool produced a given issue.
    """

    finding_id: UUID = Field(default_factory=uuid4)

    repository: str
    pull_request_number: int
    commit_sha: str

    file_path: str
    # Both optional: line/column-level tools (ESLint, Pylint, Cppcheck)
    # populate these, but Radon's complexity/maintainability scores are
    # often per-function or per-file with no single line to point at.
    line: int | None = None
    column: int | None = None

    severity: Severity
    category: FindingCategory
    rule_id: str
    message: str
    tool: str

    # Deterministic hash of the fields that identify "the same issue" —
    # computed in Phase 7. Required here (not Optional) because a Finding
    # without one isn't usable for deduplication; Phase 7 is what
    # populates it before a Finding is considered complete.
    fingerprint: str

    # SQALE-style per-issue remediation cost. Optional because not every
    # rule has an assigned cost yet (Phase 8 builds that mapping) — a
    # Finding can exist before its remediation cost is known.
    remediation_minutes: int | None = None
