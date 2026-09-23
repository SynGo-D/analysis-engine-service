import json
from dataclasses import dataclass, field
from typing import Any

from analysis_engine.agents.runtime.provider import Completion, CompletionRequest, ProviderError, ToolCall, Usage


def call(name: str, arguments: dict | str, call_id: str | None = None) -> ToolCall:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ToolCall(call_id=call_id or f"call_{name}_{abs(hash(args)) % 10_000}", name=name, arguments=args)


def turn(*calls: ToolCall, text: str = "", usage: Usage | None = None, refusal: str | None = None) -> Completion:
    return Completion(
        tool_calls=list(calls), text=text, refusal=refusal,
        usage=usage or Usage(input_tokens=1_000, cached_tokens=0, output_tokens=100),
        output_items=[{"type": "function_call", "call_id": c.call_id} for c in calls],
    )


@dataclass
class FakeProvider:
    """Plays back scripted completions and records every request — no network, no cost."""

    script: list[Completion | Exception]
    requests: list[CompletionRequest] = field(default_factory=list)

    async def complete(self, request: CompletionRequest) -> Completion:
        # Snapshot: the loop keeps appending to the same list afterwards.
        self.requests.append(CompletionRequest(**{**request.__dict__, "items": list(request.items)}))
        if not self.script:
            raise AssertionError("FakeProvider ran out of scripted turns")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def user_message(self, text: str) -> dict[str, Any]:
        return {"role": "user", "content": text}

    def tool_result(self, call_id: str, output: str) -> dict[str, Any]:
        return {"type": "function_call_output", "call_id": call_id, "output": output}


__all__ = ["FakeProvider", "ProviderError", "Usage", "call", "turn"]
