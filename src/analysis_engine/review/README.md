# review/

Stage 2 of the pipeline: the AI review of one pull request
(docs/agent-architecture.md §3, §10, §11).

## Flow

```text
AnalysisOrchestrator (inside the checkout)
  └─ linter result built → on_result(result)      saved first; dashboard can show it
  └─ ReviewOrchestrator.run
       skip?  no provider · no diff · too large · nothing reviewable → "skipped"
       on_review(running)                          dashboard shows "in progress"
       ContextPackBuilder → Reviewer agent → validate_review → rank_and_cap
       on_review(completed | failed)
```

## Guarantees

- **The linter result never waits for, or depends on, the review.** It
  is saved before the review starts. A review that fails, times out or is
  refused is saved as `failed` with its error and whatever it cost.
- **Never raises.** `ReviewOrchestrator.run` always returns an
  `AgentReview`.
- **Only checked claims are shown.** `validate_review` drops issues whose
  quotes aren't in the code, whose files or lines don't exist, or that
  cite linter findings that don't exist. Dropped issues and dropped triage
  entries are stored with the reason, for evaluation. They aren't shown.
- **At most 7 issues**, ranked by severity × confidence, with overlapping
  ones merged (`reporter.py`, plain code).

## Enabling it

Set `OPENAI_API_KEY` in `.env`. Without it, every review is recorded as
`skipped` / `disabled` and linting is unchanged. `AGENT_REVIEW_ENABLED=false`
turns it off even when a key is present.

## Trying it on a real PR

```bash
# free: what the Reviewer would read, and what that costs
.venv/bin/python scripts/preview_context_pack.py <clone_url> <branch> <target>
# paid (about $0.001 on gpt-5.6-luna, hard-capped): the real review
.venv/bin/python scripts/review_pr.py <clone_url> <branch> <target> --pr <n> --save
```
