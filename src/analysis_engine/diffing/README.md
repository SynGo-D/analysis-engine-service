# diffing/

Works out **what a pull request changed**, and connects that to the linter
findings and the repository index.

The linters analyze the whole repository, because the metrics are
repository-wide. That's the right scope for a quality dashboard but the
wrong one for reviewing a PR: out of 400 warnings, the ones that matter
are the few on lines the PR touched. This package gives every result a
pull-request view without running anything twice.

This is Phase 0 of the agent architecture
([docs/agent-architecture.md](../../../docs/agent-architecture.md) §5.1, §5.5).
No model is involved: everything here is plain git and set arithmetic.

## Flow

```text
job.target_branch
   │
   ▼
DiffExtractor.extract()
   fetch target branch ──► git merge-base HEAD origin/<target>
   (deepen 50 → 250 → 1000 commits if the histories don't meet yet)
   │
   ├─ git diff --numstat -z      every changed file + line counts
   │     └─ generated files (lockfiles, dist/, *.min.js) set aside
   └─ git diff --unified=0       exact changed line numbers, per file
   │
   ▼
PullRequestChanges (domain/change_set.py)
   │
   ├─ mark_findings()     Finding.metadata["on_changed_line"] = True/False
   └─ changed_symbols()   innermost function/method/class per changed line,
                          with how many known callers it has
```

## Decisions worth knowing

- **Diff against the merge base, not the target branch's tip.** If `main`
  moved on after the PR branched, a tip-to-tip diff would include every
  commit merged there since. The merge base is the PR's own starting point.
- **Two diff calls.** `--numstat -z` handles any filename and costs almost
  nothing, so it decides the file list and whether the PR is too big. Only
  then is the full `--unified=0` patch requested, with generated files
  excluded so a 10,000-line lockfile never reaches this process.
- **Zero context lines.** Each hunk header then states exactly which new
  lines were added. No counting through context lines.
- **Pure deletions** leave no new line behind, so they're recorded as
  `deletion_points` (the line just before) to map them to the function
  they happened in. They are *not* treated as changed lines for findings.
- **Only the innermost symbol.** Editing a method changes that method.
  Listing its class too would double what an agent later reads about the
  same edit.
- **Never fails the job.** No target branch, histories too far apart, git
  errors and bugs all end as `status: "unavailable"` with a reason. The
  linter result is complete either way.
- **Repository config can't interfere.** Every diff runs with
  `--no-ext-diff --no-textconv`, and branch names go through the same
  validation as the clone (`workspace/git_client.py`).

## Unavailable reasons

| Reason | Meaning |
|---|---|
| `no_target_branch` | The job didn't say which branch the PR targets (older messages) |
| `no_merge_base` | No common ancestor within 1,000 commits of history |
| `too_large` | Over 20,000 changed lines. The file list is kept; per-line data isn't |
| `error` | git failed, or the target branch doesn't exist |
