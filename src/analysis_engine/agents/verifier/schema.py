from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..reviewer.schema import Evidence

Question = Literal[
    "evidence_matches", "reachable", "guarded_elsewhere", "already_handled", "intended", "material", "rule_applies"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Check(_Strict):
    question: Question
    # A plain yes/no about the *issue*, not about the question. The first
    # version asked for "issue_stands | issue_refuted" per question, and the
    # model answered "was this intended? — no" with "issue_refuted",
    # discarding five real bugs in evaluation.
    refutes_issue: bool = Field(description="true ONLY if this answer shows the reported issue is not a real problem.")
    note: str = Field(description="One sentence.", max_length=300)


class VerifierOutput(_Strict):
    """Agent 2's answer (docs/agent-architecture.md §8.2), delivered via submit_verdict."""

    verdict: Literal["keep", "drop"]
    reason: str = Field(description="The strongest argument for the verdict, in one or two sentences.", max_length=500)
    checks: list[Check] = Field(max_length=6)
    adjusted_severity: Literal["high", "medium", "low"] | None = Field(
        description="Lower severity if the impact is smaller than claimed; null to keep it."
    )
    counter_evidence: list[Evidence] = Field(
        description="When dropping because of something in the code, cite it here with an exact quote.", max_length=3
    )
