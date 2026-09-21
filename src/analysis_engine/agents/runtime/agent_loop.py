import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from ...retrieval import ToolCallRecord, ToolExecutor, ToolSpec
from .pricing import price_for
from .provider import Completion, CompletionRequest, LLMProvider, ProviderError, Usage

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

StopReason = Literal[
    "submitted",          # the agent delivered a valid answer
    "invalid_output",     # it delivered an answer that failed validation twice
    "budget_exhausted",   # rounds or cost ran out before it answered
    "timeout",
    "refused",
    "provider_error",
]

# When fewer output tokens than this remain, the agent is told to answer
# now: a final answer needs room, and running out mid-answer wastes the
# whole review.
_FINAL_ANSWER_RESERVE_TOKENS = 1_500
# Past this share of the cost cap, the agent is forced to answer on its
# next call rather than explore further.
_COST_WIND_DOWN = 0.8


@dataclass(frozen=True)
class AgentBudget:
    """Hard limits for one agent run (docs/agent-architecture.md §6.3)."""

    max_rounds: int
    max_output_tokens: int
    max_seconds: float
    max_cost_usd: float


@dataclass
class CallTrace:
    """One model call: what it cost and what it did. Stored with the review."""

    round: int
    model: str
    usage: Usage
    cost_usd: float | None
    duration_ms: int
    tool_calls: list[str]
    forced_final: bool


@dataclass
class AgentRun(Generic[T]):
    output: T | None
    stop_reason: StopReason
    usage: Usage = field(default_factory=Usage)
    cost_usd: float | None = 0.0
    calls: list[CallTrace] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    error: str | None = None

    @property
    def rounds(self) -> int:
        return len(self.calls)


async def run_agent(
    provider: LLMProvider,
    *,
    model: str,
    instructions: str,
    user_content: str,
    executor: ToolExecutor,
    tool_specs: tuple[ToolSpec, ...],
    final_tool: ToolSpec,
    output_model: type[T],
    budget: AgentBudget,
    reasoning_effort: str | None = None,
    cache_key: str | None = None,
) -> AgentRun[T]:
    """
    Runs one agent to completion: the model reads, calls tools, and
    delivers its answer by calling `final_tool`, whose arguments must
    validate as `output_model`.

    The answer arrives as a tool call rather than free text because the
    tool's schema is enforced by the API in strict mode — the model can't
    return prose, a half-formed object or extra fields. `output_model`
    re-checks it anyway, and a failed answer gets exactly one chance to
    be corrected.

    Every budget is enforced here, not trusted to the model. Near the
    limit the model is *forced* to call `final_tool` (tool_choice), so a
    run ends with the best answer available rather than nothing.
    """
    state = AgentRun[T](output=None, stop_reason="budget_exhausted", tool_calls=executor.calls)
    price = price_for(model)

    try:
        async with asyncio.timeout(budget.max_seconds):
            await _loop(provider, state, price, model=model, instructions=instructions,
                        user_content=user_content, executor=executor, tools=(*tool_specs, final_tool),
                        final_tool=final_tool, output_model=output_model, budget=budget,
                        reasoning_effort=reasoning_effort, cache_key=cache_key)
    except TimeoutError:
        state.stop_reason = "timeout"
        state.error = f"Stopped after {budget.max_seconds:.0f}s."
    except ProviderError as error:
        state.stop_reason = "provider_error"
        state.error = str(error)

    logger.info(
        "agent run: %s after %d round(s), %d tool call(s), %d in / %d cached / %d out tokens, $%s",
        state.stop_reason, state.rounds, len(state.tool_calls), state.usage.input_tokens,
        state.usage.cached_tokens, state.usage.output_tokens,
        f"{state.cost_usd:.5f}" if state.cost_usd is not None else "unknown",
    )
    return state


async def _loop(provider, state: AgentRun, price, *, model, instructions, user_content, executor,
                tools, final_tool, output_model, budget, reasoning_effort, cache_key) -> None:
    items: list = [provider.user_message(user_content)]
    force_final = False
    repair_used = False
    # A repair is always possible, even when the bad answer came on the last
    # round: one extra round is allowed for it. Without this, a malformed
    # final answer at the limit ended the run with nothing (seen in
    # evaluation as "verifier budget exhausted").
    last_round = budget.max_rounds

    round_number = 0
    while round_number < last_round:
        round_number += 1
        remaining_tokens = budget.max_output_tokens - state.usage.output_tokens
        spent = state.cost_usd or 0.0

        if spent >= budget.max_cost_usd:
            state.error = f"Cost cap ${budget.max_cost_usd:.2f} reached."
            return
        if (
            round_number >= budget.max_rounds
            or remaining_tokens < _FINAL_ANSWER_RESERVE_TOKENS
            or spent >= budget.max_cost_usd * _COST_WIND_DOWN
        ):
            force_final = True

        started = time.monotonic()
        completion: Completion = await provider.complete(CompletionRequest(
            model=model,
            instructions=instructions,
            items=items,
            tools=tools,
            # The final answer may use the reserve even when the budget is
            # nearly spent; everything else gets only what's left.
            max_output_tokens=max(remaining_tokens, _FINAL_ANSWER_RESERVE_TOKENS),
            forced_tool=final_tool.name if force_final else None,
            reasoning_effort=reasoning_effort,
            cache_key=cache_key,
        ))
        _record(state, price, model, round_number, completion, started, force_final)
        items.extend(completion.output_items)

        if completion.refusal:
            state.stop_reason = "refused"
            state.error = completion.refusal[:300]
            return

        final_calls = [c for c in completion.tool_calls if c.name == final_tool.name]
        if final_calls:
            answer = final_calls[0]
            try:
                state.output = output_model.model_validate_json(answer.arguments)
                state.stop_reason = "submitted"
                return
            except (ValidationError, json.JSONDecodeError) as error:
                if repair_used:
                    state.stop_reason = "invalid_output"
                    state.error = _first_line(error)
                    return
                repair_used = True
                force_final = True
                last_round = max(last_round, round_number + 1)
                # Every call in the turn must be answered before the next
                # request, or the API rejects it.
                for call in completion.tool_calls:
                    message = (
                        f"Your answer failed validation: {_first_line(error)}. Call {final_tool.name} again, corrected."
                        if call is answer else "Not run: answer first."
                    )
                    items.append(provider.tool_result(call.call_id, message))
                continue

        if completion.tool_calls:
            # Read-only tools, so running a turn's calls concurrently is safe.
            results = await asyncio.gather(*(executor.execute(c.name, c.arguments) for c in completion.tool_calls))
            items.extend(provider.tool_result(c.call_id, r) for c, r in zip(completion.tool_calls, results))
            continue

        # Text instead of a tool call: the answer only counts via final_tool.
        items.append(provider.user_message(f"Deliver your answer by calling {final_tool.name}."))
        force_final = True


def _record(state: AgentRun, price, model: str, round_number: int, completion: Completion,
            started: float, forced: bool) -> None:
    usage = completion.usage
    cost = price.cost(usage.input_tokens, usage.cached_tokens, usage.cache_write_tokens, usage.output_tokens) if price else None

    state.usage = state.usage + usage
    state.cost_usd = None if cost is None or state.cost_usd is None else state.cost_usd + cost
    state.calls.append(CallTrace(
        round=round_number, model=model, usage=usage, cost_usd=cost,
        duration_ms=int((time.monotonic() - started) * 1000),
        tool_calls=[c.name for c in completion.tool_calls], forced_final=forced,
    ))


def _first_line(error: Exception) -> str:
    """A validation error, compacted to one line the model can act on."""
    if isinstance(error, ValidationError):
        problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()[:5]]
        return "; ".join(problems)[:500]
    return (str(error).splitlines() or [type(error).__name__])[0][:300]
