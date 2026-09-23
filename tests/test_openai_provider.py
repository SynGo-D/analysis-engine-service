import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseReasoningItem, ResponseUsage

from analysis_engine.agents.reviewer import SUBMIT_REVIEW
from analysis_engine.agents.runtime import CompletionRequest, ProviderError
from analysis_engine.agents.runtime.openai_provider import OpenAIProvider
from analysis_engine.retrieval import TOOL_SPECS


class _FakeResponses:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.params = reply, error, None

    async def create(self, **params):
        self.params = params
        if self.error:
            raise self.error
        return self.reply


def _provider(reply=None, error=None):
    responses = _FakeResponses(reply, error)
    return OpenAIProvider(api_key="unused", client=SimpleNamespace(responses=responses)), responses


def _usage(**details):
    return ResponseUsage.model_validate({
        "input_tokens": 5_000, "output_tokens": 300, "total_tokens": 5_300,
        "input_tokens_details": {"cached_tokens": details.get("cached", 0), "cache_write_tokens": details.get("written", 0)},
        "output_tokens_details": {"reasoning_tokens": details.get("reasoning", 0)},
    })


def _response(*items, status="completed", incomplete=None, usage=None):
    return SimpleNamespace(output=list(items), status=status, incomplete_details=incomplete, usage=usage or _usage())


def _request(**overrides):
    base = dict(model="gpt-5.6-luna", instructions="Review.", items=[{"role": "user", "content": "pack"}],
                tools=(*TOOL_SPECS, SUBMIT_REVIEW), max_output_tokens=2_000, reasoning_effort="low",
                cache_key="codepulse-reviewer")
    return CompletionRequest(**{**base, **overrides})


def test_sends_a_stateless_strict_cached_request():
    provider, responses = _provider(_response())
    asyncio.run(provider.complete(_request()))
    p = responses.params

    assert p["store"] is False                                    # OpenAI keeps no copy
    assert p["include"] == ["reasoning.encrypted_content"]        # reasoning can be passed back without storing
    assert all(t["strict"] is True and t["type"] == "function" for t in p["tools"])
    assert [t["name"] for t in p["tools"]][-1] == "submit_review"
    assert p["reasoning"] == {"effort": "low"}
    assert p["prompt_cache_key"] == "codepulse-reviewer"
    assert "tool_choice" not in p


def test_forcing_the_final_tool_sets_tool_choice():
    provider, responses = _provider(_response())
    asyncio.run(provider.complete(_request(forced_tool="submit_review", reasoning_effort=None)))

    assert responses.params["tool_choice"] == {"type": "function", "name": "submit_review"}
    assert "reasoning" not in responses.params


def test_parses_tool_calls_usage_and_keeps_reasoning_items_to_pass_back():
    reasoning = ResponseReasoningItem.model_validate({"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "opaque"})
    tool_call = ResponseFunctionToolCall.model_validate(
        {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": '{"path": "a.py"}', "id": "fc_1", "status": "completed"})
    provider, _ = _provider(_response(reasoning, tool_call, usage=_usage(cached=4_000, written=500, reasoning=120)))

    completion = asyncio.run(provider.complete(_request()))

    assert [(c.call_id, c.name) for c in completion.tool_calls] == [("call_1", "read_file")]
    assert completion.usage.cached_tokens == 4_000 and completion.usage.cache_write_tokens == 500
    assert completion.usage.reasoning_tokens == 120
    assert [i["type"] for i in completion.output_items] == ["reasoning", "function_call"]
    assert completion.output_items[0]["encrypted_content"] == "opaque"


def test_reports_text_refusals_and_truncation():
    message = ResponseOutputMessage.model_validate({
        "id": "m1", "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": "Hello", "annotations": []}, {"type": "refusal", "refusal": "No."}],
    })
    provider, _ = _provider(_response(message, status="incomplete", incomplete=SimpleNamespace(reason="max_output_tokens")))

    completion = asyncio.run(provider.complete(_request()))

    assert completion.text == "Hello" and completion.refusal == "No." and completion.truncated


@pytest.mark.parametrize("error, expected", [
    (openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")), "unavailable"),
    (openai.BadRequestError("bad", response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
                            body={"error": {"message": "Unsupported parameter: reasoning"}}), "Unsupported parameter"),
])
def test_api_errors_become_provider_errors(error, expected):
    provider, _ = _provider(error=error)

    with pytest.raises(ProviderError, match=expected):
        asyncio.run(provider.complete(_request()))


def test_the_submit_review_schema_is_strict_everywhere():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    schema = SUBMIT_REVIEW.parameters
    walk(schema)
    # The bug this guards: property *names* were once filtered as if they
    # were schema keywords, silently dropping fields.
    assert set(schema["$defs"]["Evidence"]["properties"]) == {"type", "ref", "quote"}
    assert set(schema["$defs"]["CandidateIssue"]["properties"]) >= {"title", "evidence", "line_start", "suggested_fix"}
