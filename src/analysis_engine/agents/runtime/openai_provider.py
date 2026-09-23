import logging
from typing import Any

import openai
from openai import AsyncOpenAI

from .provider import Completion, CompletionRequest, ProviderError, ToolCall, Usage

logger = logging.getLogger(__name__)


class OpenAIProvider:
    """
    LLMProvider over OpenAI's Responses API.

    Choices made for cost and privacy:

    - `store=False`: OpenAI keeps no copy of the conversation. That means
      the conversation is resent each round (it would be anyway for
      billing purposes), and reasoning comes back encrypted
      (`include=["reasoning.encrypted_content"]`) so it can be passed back
      as the docs require for reasoning models, without being stored.
    - Tools are sent in a fixed order with `strict: true`, and instructions
      never change within a review. Prompt caching only works on an
      identical prefix, and a cached token costs a tenth of a fresh one.
    - Retries: the SDK retries rate limits and 5xx twice with backoff.
      Anything else surfaces as ProviderError.
    """

    def __init__(self, api_key: str, timeout: float = 60.0, client: AsyncOpenAI | None = None):
        self._client = client or AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=2)

    async def complete(self, request: CompletionRequest) -> Completion:
        params: dict[str, Any] = {
            "model": request.model,
            "instructions": request.instructions,
            "input": request.items,
            "tools": [
                {
                    "type": "function",
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                    "strict": True,
                }
                for spec in request.tools
            ],
            "max_output_tokens": request.max_output_tokens,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if request.forced_tool:
            params["tool_choice"] = {"type": "function", "name": request.forced_tool}
        if request.reasoning_effort:
            params["reasoning"] = {"effort": request.reasoning_effort}
        if request.cache_key:
            params["prompt_cache_key"] = request.cache_key

        try:
            response = await self._client.responses.create(**params)
        except (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError) as error:
            # Already retried by the SDK; still failing means this review
            # can't proceed right now.
            raise ProviderError(f"OpenAI unavailable: {type(error).__name__}") from error
        except openai.APIStatusError as error:
            raise ProviderError(f"OpenAI rejected the request ({error.status_code}): {_message(error)}") from error

        return _to_completion(response)

    def user_message(self, text: str) -> dict[str, Any]:
        return {"role": "user", "content": text}

    def tool_result(self, call_id: str, output: str) -> dict[str, Any]:
        return {"type": "function_call_output", "call_id": call_id, "output": output}


def _to_completion(response: Any) -> Completion:
    tool_calls: list[ToolCall] = []
    texts: list[str] = []
    refusal: str | None = None
    output_items: list[Any] = []

    for item in response.output or []:
        # Passed back verbatim (minus nulls) on the next round, as the
        # Responses API expects for function calls and reasoning items.
        output_items.append(item.model_dump(exclude_none=True))

        if item.type == "function_call":
            tool_calls.append(ToolCall(call_id=item.call_id, name=item.name, arguments=item.arguments))
        elif item.type == "message":
            for part in item.content or []:
                if part.type == "output_text":
                    texts.append(part.text)
                elif part.type == "refusal":
                    refusal = part.refusal

    usage = response.usage
    input_details = getattr(usage, "input_tokens_details", None) if usage else None
    output_details = getattr(usage, "output_tokens_details", None) if usage else None

    truncated = (
        response.status == "incomplete"
        and getattr(response.incomplete_details, "reason", None) == "max_output_tokens"
    )

    return Completion(
        tool_calls=tool_calls,
        text="\n".join(texts),
        usage=Usage(
            input_tokens=usage.input_tokens if usage else 0,
            cached_tokens=getattr(input_details, "cached_tokens", 0) or 0,
            cache_write_tokens=getattr(input_details, "cache_write_tokens", 0) or 0,
            output_tokens=usage.output_tokens if usage else 0,
            reasoning_tokens=getattr(output_details, "reasoning_tokens", 0) or 0,
        ),
        output_items=output_items,
        refusal=refusal,
        truncated=truncated,
    )


def _message(error: openai.APIStatusError) -> str:
    body = error.body if isinstance(error.body, dict) else {}
    detail = body.get("error", body) if isinstance(body, dict) else {}
    return str(detail.get("message", error.message) if isinstance(detail, dict) else error.message)[:300]
