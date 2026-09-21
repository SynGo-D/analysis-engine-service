from pathlib import Path

from ...config import settings
from ...retrieval import TOOL_SPECS, ToolExecutor, ToolSpec
from ..runtime.agent_loop import AgentBudget, AgentRun, run_agent
from ..runtime.provider import LLMProvider
from ..runtime.strict_schema import strict_json_schema
from .schema import VerifierOutput

INSTRUCTIONS = (Path(__file__).parent / "prompt.md").read_text()

SUBMIT_VERDICT = ToolSpec(
    name="submit_verdict",
    description="Deliver your verdict on the issue. Call exactly once, at the end.",
    parameters=strict_json_schema(VerifierOutput),
)


def verifier_budget() -> AgentBudget:
    return AgentBudget(
        max_rounds=settings.verifier_max_rounds,
        max_output_tokens=settings.verifier_max_output_tokens,
        max_seconds=settings.verifier_max_seconds,
        max_cost_usd=settings.verifier_max_cost_usd,
    )


async def run_verifier(provider: LLMProvider, content: str, executor: ToolExecutor) -> AgentRun[VerifierOutput]:
    """Agent 2: one call per reported issue, with a fresh context that never sees the Reviewer's reasoning."""
    return await run_agent(
        provider,
        model=settings.verifier_model,
        instructions=INSTRUCTIONS,
        user_content=content,
        executor=executor,
        tool_specs=TOOL_SPECS,
        final_tool=SUBMIT_VERDICT,
        output_model=VerifierOutput,
        budget=verifier_budget(),
        reasoning_effort=settings.verifier_reasoning_effort or None,
        # Shared by every Verifier call: instructions and tools are the
        # cached prefix across all issues in all reviews.
        cache_key="codepulse-verifier",
    )
