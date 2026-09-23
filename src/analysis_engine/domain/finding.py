from typing import Any, Literal

from pydantic import BaseModel, Field
from uuid import UUID, uuid4

Severity = Literal["error", "warning", "info"]

FindingCategory = Literal[
    "bug",
    "vulnerability",
    "code_smell",
    "style",
    "complexity",
    "cognitive_complexity",
    "maintainability",
    "unused_code",
]


class Finding(BaseModel):
    """
    A single normalized issue — the common shape every tool's raw output
    (ESLint, Pylint, Radon, Bandit, ...) gets translated into, so nothing
    downstream needs to know which tool or language produced a given
    finding.
    """

    finding_id: UUID = Field(default_factory=uuid4)

    repository: str
    pull_request_number: int
    commit_sha: str

    file_path: str
    # All four optional: line/column-level tools (ESLint, Pylint, Bandit)
    # populate these, but Radon's complexity/maintainability scores are
    # often per-function or per-file with no single line to point at.
    # end_line/end_column are populated only when a tool's own output
    # reports a range (Pylint does; ESLint's JSON also includes them but
    # this service doesn't currently read them, Bandit reports a
    # line_range instead of endLine/endColumn).
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None

    severity: Severity
    category: FindingCategory
    rule_id: str
    message: str
    tool: str

    # Deterministic hash of the fields that identify "the same issue" —
    # computed by FindingNormalizer. Required here (not Optional) because
    # a Finding without one isn't usable for deduplication.
    fingerprint: str

    # SQALE-style per-issue remediation cost. Optional because not every
    # rule has an assigned cost yet — a Finding can exist before its
    # remediation cost is known.
    remediation_minutes: int | None = None

    # Tool-specific information that doesn't fit the common schema above
    # (e.g. Bandit's confidence level and CWE reference, Pylint's raw
    # "type"/"obj" fields) — kept instead of being discarded or forced
    # into a generic field that wouldn't fit every tool.
    metadata: dict[str, Any] = Field(default_factory=dict)
