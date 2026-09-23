from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...domain.business_rule import RULE_ID_PATTERN
from ..reviewer.schema import Evidence


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuggestedRuleOut(_Strict):
    rule_id: str = Field(pattern=RULE_ID_PATTERN, max_length=40, description="e.g. REFUND-APPROVAL")
    rule: str = Field(min_length=10, max_length=400)
    applies_to: list[str] = Field(max_length=8, description="Path globs, e.g. 'payments/**'. Empty = everywhere.")
    severity: Literal["high", "medium", "low"]
    rationale: str = Field(description="Why the product needs this rule, in one sentence.", max_length=300)
    source: Evidence


class RuleMinerOutput(_Strict):
    """Agent 3's answer (docs/agent-architecture.md §8.3), delivered via submit_rules."""

    suggestions: list[SuggestedRuleOut] = Field(max_length=12)
