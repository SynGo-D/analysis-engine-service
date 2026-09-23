from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The Reviewer's answer, delivered as the arguments of its `submit_review`
# tool call (docs/agent-architecture.md §8.1). Field descriptions are part
# of the tool schema the model sees, so they double as instructions.
#


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Strict):
    type: Literal["code_location", "linter_finding", "business_rule", "call_path", "test"]
    ref: str = Field(
        description="code_location: 'path:line' or 'path:start-end'. linter_finding: the [ref] shown "
                    "for the finding. business_rule: the rule id, e.g. BR-PRICING-001. call_path: 'a → b → c'. "
                    "test: 'path' or 'path::test_name'.",
        max_length=300,
    )
    quote: str | None = Field(
        description="For code_location: the exact code at that location, copied character for character. "
                    "Otherwise null.",
        max_length=600,
    )


class TriagedFinding(_Strict):
    ref: str = Field(description="The linter finding's [ref].", max_length=20)
    importance: Literal["high", "medium", "low"]
    reason: str = Field(description="Why it matters for THIS change, in one sentence.", max_length=400)


class CandidateIssue(_Strict):
    title: str = Field(description="One line.", max_length=140)
    category: Literal["correctness", "security", "business_rule"]
    severity: Literal["high", "medium", "low"]
    confidence: float = Field(ge=0, le=1, description="How sure you are this is a real problem, 0-1.")
    file_path: str = Field(max_length=300)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    explanation: str = Field(description="What is wrong and what happens because of it.", max_length=1200)
    evidence: list[Evidence] = Field(min_length=1, max_length=5)
    suggested_fix: str | None = Field(description="Short unified diff, or null.", max_length=1500)


class RuleCheckOut(_Strict):
    rule_id: str = Field(max_length=40)
    outcome: Literal["violated", "satisfied", "not_applicable"]
    note: str = Field(description="One sentence on why.", max_length=300)


class ReviewerOutput(_Strict):
    summary: str = Field(description="2-4 plain sentences: what this PR does.", max_length=800)
    areas_touched: list[str] = Field(description="Short names of the product areas changed.", max_length=6)
    linter_triage: list[TriagedFinding] = Field(
        description="Only linter findings that matter for this change. Empty if none do.", max_length=10
    )
    candidates: list[CandidateIssue] = Field(
        description="Problems the linters can't see. Empty is a good answer when there are none.", max_length=10
    )
    rule_checks: list[RuleCheckOut] = Field(
        default_factory=list,
        description="One entry per business rule listed in the context. Empty if none are listed.", max_length=30,
    )
