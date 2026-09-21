# evaluation/

Scores the AI review against pull requests with planted bugs
(docs/agent-architecture.md §12). Without a score, a prompt or model
change can't be judged better or worse, only different.

```bash
.venv/bin/python -m analysis_engine.evaluation --dry-run            # free: everything except the model
.venv/bin/python -m analysis_engine.evaluation                      # real run, about $0.01 on gpt-5.6-luna
.venv/bin/python -m analysis_engine.evaluation --cases py-,clean-   # a subset, by id prefix
.venv/bin/python -m analysis_engine.evaluation --model gpt-5.6-terra
```

Each case becomes a real git repository (`main` plus a `feature` branch)
and goes through the real pipeline: diff, index, linters, context pack,
Reviewer, validation. Only the clone is replaced. Results are saved to
`evaluation-results/` (git-ignored), and each run is compared with the
previous one for the same model.

## The cases (`cases.py`)

- **12 PRs with one planted bug each** (7 Python, 5 JavaScript). Every
  bug is one linters can't see: logic, intent and security. Several are
  only bugs relative to what the PR description says it does.
- **6 clean PRs**, two of them decoys: correct code that looks suspicious.
- Defects are located by a code snippet, not a line number. A report
  within 3 lines, in the same file, counts as found. `also` lists other
  places where reporting the same defect counts.

**Clean cases must really be clean.** On the first run, two "clean" cases
were flagged, and both reports were correct (a floating-point tolerance
and a log injection). The cases were fixed, not the scores.

## Metrics and targets

| Metric | Meaning | Target |
|---|---|---|
| precision | Reported issues that are real | ≥ 0.70 |
| recall | Planted bugs found | ≥ 0.50 |
| clean_false_alarm_rate | Issues reported per clean PR | ≤ 0.10 |
| p95_duration_s | Slowest 5% of reviews | ≤ 90 s |
| cost_per_pr_usd | Average spend per PR | ≤ $0.30 |

## Baseline (gpt-5.6-luna, reasoning effort low, 2026-09-21)

| | |
|---|---|
| recall | **12/12** |
| precision | **14/14 real** (0.86 as first scored, before 2 mislabelled clean cases were corrected) |
| false alarms on clean PRs | **0** after the correction |
| p95 duration | 12 s |
| cost | **$0.0006 per PR**, $0.0104 for all 18 |

## With the hard cases and the Verifier (28 PRs, 2026-09-21)

`cases_hard.py` adds 10 harder PRs: bugs among legitimate multi-file
changes, two bugs in one PR, a changed contract that breaks untouched
callers, idempotency, JWT verification, timezones, a threshold on the
wrong total, a default flipped inside a large refactor, and two larger
clean PRs.

| Run | Recall | Precision | False alarms / clean PR | $/PR |
|---|---|---|---|---|
| Reviewer only (the two earlier runs combined) | 0.95 | 0.91 | 0.12 | 0.0007 |
| + Verifier v1 | 0.71 | 1.00 | 0.00 | 0.0015 |
| + Verifier v2 (current) | **0.95** | **0.95** | **0.12** | **0.0016** |

- Still missed: the second bug in `hard-cache-two-bugs` (a cache-key
  collision). The Reviewer never proposes it.
- Still flagged: `hard-clean-order-notes`, "the constructor bypasses the
  note length limit". It's arguably true (`Order(note="x"*600)` is
  accepted), but left labelled clean: cases aren't relabelled whenever
  the model disagrees.
- Single runs vary. The same case can pass in one run and fail in the
  next, so compare changes on the whole set, and rerun anything close.

**Read this with care: the set is too easy to separate good from great.**
Every case is a few small files with a single bug, and the context packs
are 200–450 tokens. Real PRs are larger, touch many files and bury a bug
among legitimate changes. A perfect score here means the pipeline works;
it doesn't yet mean the Reviewer is good enough for real PRs. The next
cases to add are harder: multi-file PRs, bugs among noise, several bugs
in one PR, and business-rule violations (phase 5).
