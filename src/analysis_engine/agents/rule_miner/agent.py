from pathlib import Path

from ...config import settings
from ...retrieval import TOOL_SPECS, ToolExecutor, ToolSpec
from ..runtime.agent_loop import AgentBudget, AgentRun, run_agent
from ..runtime.provider import LLMProvider
from ..runtime.strict_schema import strict_json_schema
from .schema import RuleMinerOutput

INSTRUCTIONS = (Path(__file__).parent / "prompt.md").read_text()

SUBMIT_RULES = ToolSpec(
    name="submit_rules",
    description="Deliver your suggested rules. Call exactly once, at the end.",
    parameters=strict_json_schema(RuleMinerOutput),
)


async def run_rule_miner(provider: LLMProvider, content: str, executor: ToolExecutor) -> AgentRun[RuleMinerOutput]:
    """Agent 3: occasional, on demand; never part of a PR review."""
    return await run_agent(
        provider,
        model=settings.rule_miner_model,
        instructions=INSTRUCTIONS,
        user_content=content,
        executor=executor,
        tool_specs=TOOL_SPECS,
        final_tool=SUBMIT_RULES,
        output_model=RuleMinerOutput,
        budget=AgentBudget(
            max_rounds=settings.rule_miner_max_rounds,
            max_output_tokens=settings.rule_miner_max_output_tokens,
            max_seconds=settings.rule_miner_max_seconds,
            max_cost_usd=settings.rule_miner_max_cost_usd,
        ),
        reasoning_effort=settings.reviewer_reasoning_effort or None,
        cache_key="codepulse-rule-miner",
    )
