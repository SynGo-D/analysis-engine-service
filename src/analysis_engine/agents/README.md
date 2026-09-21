# agents/

The AI agents (docs/agent-architecture.md §6, §8), and the runtime they
run on.

```text
agents/
├── runtime/          shared machinery, no agent-specific knowledge
│   ├── provider.py          LLMProvider protocol + neutral types
│   ├── openai_provider.py   OpenAI Responses API adapter
│   ├── agent_loop.py        the tool loop, budgets, forced final answer
│   ├── strict_schema.py     Pydantic model → OpenAI strict JSON schema
│   └── pricing.py           per-model prices; every reported cost uses it
├── verifier/         Agent 2 (phase 4): tries to refute each reported issue
│   ├── prompt.md, schema.py, agent.py   (its verdict is applied in review/verification.py)
└── reviewer/         Agent 1 (phase 2)
    ├── prompt.md            instructions (sent with every call)
    ├── schema.py            ReviewerOutput: what submit_review must contain
    ├── validation.py        checks every claim against the repository
    └── agent.py             wires the above into run_agent
```

## What an agent is here

A loop, not a single prompt:

1. Send instructions + the context pack.
2. The model either calls read-only tools (their results are sent back,
   and the loop repeats) or calls its **final tool** (`submit_review`).
3. The final tool's arguments are the answer. The API enforces their
   schema in strict mode, and Pydantic validates them again. A failed
   answer gets exactly one chance to be corrected.

Delivering the answer as a tool call rather than as text means the model
can't return prose, a half-formed object or extra fields.

## Budgets are enforced by code, not trusted to the model

Every run has hard limits on rounds, output tokens, seconds and dollars
(settings: `REVIEWER_MAX_*`, `REVIEW_MAX_COST_USD`). Near any limit, the
loop sets `tool_choice` so the model *must* submit on its next call. A run
ends with the best answer available, not an abrupt cutoff. Unknown models
(not in `pricing.py`) report cost as unknown, and then only the token and
round limits apply.

## Cost choices

- **Stateless** (`store=False`): OpenAI keeps no copy. Reasoning is
  returned encrypted and passed back, as the docs require for reasoning
  models.
- **Prompt caching:** instructions and tool definitions are identical on
  every call, and in a fixed order, so they're cached. On the second real
  review, 2,381 of 2,384 input tokens came from the cache.
- **Low reasoning effort** by default: reasoning tokens are billed as
  output, the most expensive kind.

## Testing without spending

`tests/fake_provider.py` plays back scripted model turns. Every agent
behaviour (tool round trips, repair, forced answers, every budget limit,
refusals, provider failures, timeouts) is tested against it. Only
`scripts/review_pr.py` calls the real API.
