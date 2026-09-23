import asyncio

import pytest
from pydantic import BaseModel, Field

from analysis_engine.agents.runtime import AgentBudget, Usage, run_agent
from analysis_engine.agents.runtime.pricing import OPENAI_PRICES
from analysis_engine.agents.runtime.strict_schema import strict_json_schema
from analysis_engine.indexing import CodeIndexer
from analysis_engine.retrieval import TOOL_SPECS, RetrievalTools, ToolExecutor, ToolSpec

from .fake_provider import FakeProvider, ProviderError, call, turn


class Answer(BaseModel):
    verdict: str = Field(max_length=20)


SUBMIT = ToolSpec("submit", "Deliver the answer.", strict_json_schema(Answer))
GOOD = {"verdict": "fine"}
BUDGET = AgentBudget(max_rounds=5, max_output_tokens=8_000, max_seconds=10, max_cost_usd=1.0)


@pytest.fixture
def executor(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    return ToolExecutor(RetrievalTools(tmp_path, CodeIndexer().build(tmp_path), []))


def _run(provider, executor, budget=BUDGET, model="gpt-5.6-luna"):
    return asyncio.run(run_agent(
        provider, model=model, instructions="Review.", user_content="the pack", executor=executor,
        tool_specs=TOOL_SPECS, final_tool=SUBMIT, output_model=Answer, budget=budget,
    ))


def test_runs_tools_then_accepts_the_submitted_answer(executor):
    provider = FakeProvider([turn(call("read_file", {"path": "a.py", "start_line": None, "end_line": None})),
                             turn(call("submit", GOOD))])

    run = _run(provider, executor)

    assert run.stop_reason == "submitted" and run.output.verdict == "fine"
    assert run.rounds == 2
    # The tool's real output went back to the model on the second request.
    tool_outputs = [i for i in provider.requests[1].items if i.get("type") == "function_call_output"]
    assert "a.py (lines 1-2 of 2)" in tool_outputs[0]["output"]


def test_every_request_carries_the_same_tools_in_the_same_order(executor):
    # Prompt caching only matches an identical prefix, tools included.
    provider = FakeProvider([turn(call("find_symbol", {"name": "f"})), turn(call("submit", GOOD))])
    _run(provider, executor)

    first, second = ([t.name for t in r.tools] for r in provider.requests)
    assert first == second and first[-1] == "submit"


def test_adds_up_usage_and_cost_including_cache_reads_and_writes(executor):
    usage = Usage(input_tokens=10_000, cached_tokens=6_000, cache_write_tokens=2_000, output_tokens=500)
    provider = FakeProvider([turn(call("find_symbol", {"name": "f"}), usage=usage), turn(call("submit", GOOD), usage=usage)])

    run = _run(provider, executor)

    price = OPENAI_PRICES["gpt-5.6-luna"]
    one_call = (2_000 * price.input + 6_000 * price.cached_input + 2_000 * price.cache_write + 500 * price.output) / 1e6
    assert run.usage.input_tokens == 20_000
    assert run.cost_usd == pytest.approx(2 * one_call)


def test_a_malformed_answer_gets_one_chance_to_be_corrected(executor):
    provider = FakeProvider([turn(call("submit", {"verdict": "x" * 50})), turn(call("submit", GOOD))])

    run = _run(provider, executor)

    assert run.stop_reason == "submitted"
    feedback = provider.requests[1].items[-1]["output"]
    assert "verdict" in feedback and "failed validation" in feedback
    assert provider.requests[1].forced_tool == "submit"


def test_a_second_malformed_answer_ends_the_run(executor):
    bad = {"verdict": "x" * 50}
    run = _run(FakeProvider([turn(call("submit", bad)), turn(call("submit", bad))]), executor)

    assert run.stop_reason == "invalid_output" and run.output is None


def test_text_instead_of_a_tool_call_is_redirected_to_submit(executor):
    provider = FakeProvider([turn(text="Looks fine to me."), turn(call("submit", GOOD))])

    run = _run(provider, executor)

    assert run.stop_reason == "submitted"
    assert provider.requests[1].forced_tool == "submit"


def test_the_last_round_forces_an_answer(executor):
    provider = FakeProvider([turn(call("find_symbol", {"name": "f"})), turn(call("submit", GOOD))])

    _run(provider, executor, budget=AgentBudget(max_rounds=2, max_output_tokens=8_000, max_seconds=10, max_cost_usd=1))

    assert [r.forced_tool for r in provider.requests] == [None, "submit"]


def test_nearing_the_cost_cap_forces_an_answer(executor):
    expensive = Usage(input_tokens=100_000, output_tokens=1_000)   # ~$0.021 on Luna
    provider = FakeProvider([turn(call("find_symbol", {"name": "f"}), usage=expensive), turn(call("submit", GOOD))])

    _run(provider, executor, budget=AgentBudget(max_rounds=5, max_output_tokens=8_000, max_seconds=10, max_cost_usd=0.025))

    assert provider.requests[1].forced_tool == "submit"


def test_stops_without_another_call_once_the_cost_cap_is_spent(executor):
    huge = Usage(input_tokens=1_000_000, output_tokens=0)          # $0.20 on Luna
    provider = FakeProvider([turn(call("find_symbol", {"name": "f"}), usage=huge)])

    run = _run(provider, executor, budget=AgentBudget(max_rounds=5, max_output_tokens=8_000, max_seconds=10, max_cost_usd=0.10))

    assert run.stop_reason == "budget_exhausted" and len(provider.requests) == 1
    assert "Cost cap" in run.error


def test_output_token_budget_forces_an_answer(executor):
    wordy = Usage(input_tokens=1_000, output_tokens=7_000)
    provider = FakeProvider([turn(call("find_symbol", {"name": "f"}), usage=wordy), turn(call("submit", GOOD))])

    _run(provider, executor)

    assert provider.requests[1].forced_tool == "submit"
    assert provider.requests[1].max_output_tokens == 1_500   # the reserve, not the 1,000 left


def test_every_parallel_tool_call_is_answered(executor):
    provider = FakeProvider([
        turn(call("find_symbol", {"name": "f"}, "c1"), call("read_file", {"path": "a.py", "start_line": None, "end_line": None}, "c2")),
        turn(call("submit", GOOD)),
    ])
    _run(provider, executor)

    answered = {i["call_id"] for i in provider.requests[1].items if i.get("type") == "function_call_output"}
    assert answered == {"c1", "c2"}


def test_provider_failure_and_refusal_end_the_run_cleanly(executor):
    failed = _run(FakeProvider([ProviderError("OpenAI unavailable")]), executor)
    refused = _run(FakeProvider([turn(refusal="I can't help with that.")]), executor)

    assert failed.stop_reason == "provider_error" and failed.output is None
    assert refused.stop_reason == "refused"


def test_times_out(executor):
    class Slow(FakeProvider):
        async def complete(self, request):
            await asyncio.sleep(5)

    run = _run(Slow([]), executor, budget=AgentBudget(max_rounds=5, max_output_tokens=8_000, max_seconds=0.1, max_cost_usd=1))

    assert run.stop_reason == "timeout"


def test_unknown_model_price_reports_cost_as_unknown(executor):
    run = _run(FakeProvider([turn(call("submit", GOOD))]), executor, model="some-future-model")

    assert run.stop_reason == "submitted" and run.cost_usd is None


def test_a_bad_answer_on_the_last_round_still_gets_its_repair(executor):
    # Found in evaluation: a malformed final answer at the round limit used
    # to end the run with nothing.
    provider = FakeProvider([
        turn(call("find_symbol", {"name": "f"})),
        turn(call("submit", {"verdict": "x" * 50})),
        turn(call("submit", GOOD)),
    ])

    run = _run(provider, executor, budget=AgentBudget(max_rounds=2, max_output_tokens=8_000, max_seconds=10, max_cost_usd=1))

    assert run.stop_reason == "submitted" and run.rounds == 3
