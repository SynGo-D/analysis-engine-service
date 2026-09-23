# retrieval/

The **read-only tools** an agent uses to look at a repository, beyond the
context pack it starts with (docs/agent-architecture.md §7).

An agent is only as good as what it can see, and every character it sees
costs money. These tools are how it looks further (read a file, follow
the call graph, search) without being handed the whole repository.

## Tools

| Tool | Returns | Limit |
|---|---|---|
| `read_file(path, start_line?, end_line?)` | Numbered lines | 300 lines per call |
| `get_symbol_source(symbol_id)` | A function/method/class, whole | 400 lines |
| `find_symbol(name)` | Symbols by name or `Class.method` | 20 |
| `callers_of(symbol_id)` | Call sites, with line numbers | 25 |
| `callees_of(symbol_id)` | What it calls | 50 |
| `list_tests_for(symbol_id)` | Test functions calling it by name | 20 |
| `search_code(text, path_glob?)` | Literal matches via `git grep` | 50, max 5 per file |
| `get_linter_findings(path?, rule_id?, changed_only?)` | Findings, errors first | 50 |

`tool_specs.py` holds the definitions the model sees and the
`ToolExecutor` that runs its calls.

## Rules every tool follows

- **Plain text out, not JSON.** It's what the model reads, it's cheaper in
  tokens, and it's easier to quote from.
- **Bounded, and says so.** Every result has a limit. When something is
  cut, the result says what, and how to get it ("call again with
  start_line=301").
- **Never raises to the agent.** A bad path, unknown id or malformed
  arguments come back as `Error: …` text the model can correct. One bad
  call must not end a review.
- **Redacted.** Output passes through `redaction.py` first. Anything in a
  prompt is sent to the model provider, so recognisable credentials
  (cloud keys, tokens, private keys, passwords in code) are replaced with
  `[REDACTED]`, while the surrounding code stays visible.
- **Confined to the checkout.** `path_guard.py` resolves every path,
  following symlinks, before checking it stays inside the workspace, and
  refuses `.git/`, where a private repository's clone URL can hold a token.
- **Literal search only.** No regex: a hostile or careless pattern could
  run for minutes.

## Why the limits matter more than they look

In a tool loop, the whole conversation is resent to the model on every
round. A tool result from round 2 is paid for again in rounds 3, 4 and 5.
Prompt caching makes repeats cheaper (a tenth of the price on OpenAI),
but an unbounded result is still paid for many times.

## Finding references

Findings are referred to by the first 10 characters of their fingerprint
(`[d3c800065e]`), not the full 64. That's about a quarter of the tokens
across dozens of findings. `refs.resolve_finding_ref` maps a reference
back, and refuses one that matches zero or several findings.
