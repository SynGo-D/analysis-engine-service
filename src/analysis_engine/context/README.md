# context/

Builds the **context pack**: the bounded bundle of facts the Reviewer
agent starts from (docs/agent-architecture.md §5.6).

The pack is the most important cost lever in the system. It's sent on
the Reviewer's first call and then resent, from the prompt cache, on
every later round of its tool loop. A tight pack is cheap and keeps the
model focused. A loose one costs more and buries the change under noise.

## Sections

| Section | Contents | Budget (chars) |
|---|---|---|
| Pull request | Title, description, branches, size | 4,000 |
| Diff | `git diff -U3` against the merge base; source files before tests, smaller before larger | 50,000 total, 15,000 per file |
| Edited functions | Full source of functions the PR *edited* (not ones it added), widest blast radius first | 35,000, max 40 |
| Callers | Known call sites of each changed function | 6,000 |
| Linter findings | Only those on changed lines, errors first | 12,000, max 60 |
| Repository map | Top-level layout, imports of changed files | 2,000 |

About 110,000 characters at most, roughly 28,000 tokens. A typical PR is
far smaller: the first real one measured came to about 1,000 tokens.

## Decisions

- **New functions aren't repeated.** A function the PR wrote from scratch
  is already whole in the diff. Only *edited* functions get their full
  source, because the diff shows just the edited lines and a little
  context around them. This alone cut the first real pack by 37%.
- **Findings on untouched lines stay out.** The agent can fetch them with
  `get_linter_findings`, but doesn't pay for them by default.
- **Every cut is announced.** A truncated section says what was left out
  and which tool retrieves it, so the agent never assumes it saw
  everything.
- **Redaction and the path guard apply here too**, the same ones the
  tools use (see `retrieval/README.md`).

## Seeing a real pack

```bash
.venv/bin/python scripts/preview_context_pack.py \
    https://github.com/SynGo-D/test-project.git "branch#1" main
```

This prints the pack and what it would cost to send on each OpenAI model.
No model is called.
