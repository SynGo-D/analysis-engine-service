from pathlib import Path

from ...config import settings
from ...retrieval import TOOL_SPECS, ToolExecutor, ToolSpec
from ..runtime.agent_loop import AgentBudget, AgentRun, run_agent
from ..runtime.provider import LLMProvider
from ..runtime.strict_schema import strict_json_schema
from .schema import ReviewerOutput

INSTRUCTIONS = (Path(__file__).parent / "prompt.md").read_text()

# Built once: the tool list must be byte-identical on every call for the
# provider's prompt cache to match (tools are part of the cached prefix).
SUBMIT_REVIEW = ToolSpec(
    name="submit_review",
    description="Deliver your review. Call exactly once, at the end.",
    parameters=strict_json_schema(ReviewerOutput),
)


def reviewer_budget() -> AgentBudget:
    return AgentBudget(
        max_rounds=settings.reviewer_max_rounds,
        max_output_tokens=settings.reviewer_max_output_tokens,
        max_seconds=settings.reviewer_max_seconds,
        max_cost_usd=settings.review_max_cost_usd,
    )


async def run_reviewer(
    provider: LLMProvider,
    context_pack: str,
    executor: ToolExecutor,
    *,
    model: str | None = None,
    budget: AgentBudget | None = None,
) -> AgentRun[ReviewerOutput]:
    """Agent 1 (docs/agent-architecture.md §8.1): reads the pack, may use tools, submits a ReviewerOutput."""
    return await run_agent(
        provider,
        model=model or settings.reviewer_model,
        instructions=INSTRUCTIONS,
        user_content=context_pack,
        executor=executor,
        tool_specs=TOOL_SPECS,
        final_tool=SUBMIT_REVIEW,
        output_model=ReviewerOutput,
        budget=budget or reviewer_budget(),
        reasoning_effort=settings.reviewer_reasoning_effort or None,
        cache_key="codepulse-reviewer",
    )
