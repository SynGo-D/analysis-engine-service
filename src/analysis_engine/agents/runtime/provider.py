from dataclasses import dataclass, field
from typing import Any, Protocol

from ...retrieval import ToolSpec


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str  # JSON text, exactly as the model produced it


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0  # already included in output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    instructions: str
    # The conversation so far, in the provider's own item format. The loop
    # never looks inside these; it only appends what the provider returns.
    items: list[Any]
    tools: tuple[ToolSpec, ...]
    max_output_tokens: int
    # None = the model decides; a tool name = it must call that tool now.
    forced_tool: str | None = None
    reasoning_effort: str | None = None
    # Groups requests that share a prompt prefix, for cache routing.
    cache_key: str | None = None


@dataclass
class Completion:
    tool_calls: list[ToolCall]
    text: str
    usage: Usage
    # Items to append to the conversation before the next request —
    # including hidden reasoning, which reasoning models need sent back.
    output_items: list[Any] = field(default_factory=list)
    refusal: str | None = None
    # The model ran out of output tokens mid-answer.
    truncated: bool = False


class ProviderError(Exception):
    """The provider failed in a way retrying won't fix (bad request, auth, model unavailable)."""


class LLMProvider(Protocol):
    """
    What the agent loop needs from a model vendor, and nothing more.

    Agents are written against this, never against a vendor SDK, so the
    model — or the vendor — is a configuration change (principle 7 in
    docs/agent-architecture.md).
    """

    async def complete(self, request: CompletionRequest) -> Completion: ...

    def user_message(self, text: str) -> Any:
        """A conversation item carrying the user's text."""

    def tool_result(self, call_id: str, output: str) -> Any:
        """A conversation item answering one tool call."""
