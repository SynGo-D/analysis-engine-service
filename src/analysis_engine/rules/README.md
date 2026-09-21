# rules/

A repository's **business rules**: the product-specific constraints a
review checks a pull request against (docs/agent-architecture.md §9). They
let a review say "this breaks *your* rule", which no linter or generic
model can know.

## Where rules come from

| Source | How | Used in reviews |
|---|---|---|
| `.codepulse/rules.yml` in the repository | Written and reviewed like code | Always, read from the PR's **target branch** |
| The dashboard | Added by a person (`POST /api/repositories/{o}/{r}/rules`) | While `active` |
| The Rule Miner | Suggested from the repository's docs, tests and code | Only after a person accepts it |

```yaml
version: 1
rules:
  - id: REFUND-APPROVAL               # upper case with hyphens; issues cite it
    rule: Refunds above 500 dollars require a manager's approval.
    applies_to: ["payments/**"]      # globs; empty = every PR
    severity: high                   # the most severe an issue citing it can be
    rationale: Fraud control.
```

## Decisions

- **Rules come from the target branch.** A PR that deletes or weakens the
  rule it breaks is still reviewed against the rule. The Reviewer is told
  when a PR edits the rules file. An evaluation case checks this.
- **One bad rule doesn't disable the rest.** Each rule is validated on its
  own. Problems are reported on the review (`rule_errors`) and the valid
  rules still apply. `yaml.safe_load` only.
- **The file wins over stored rules** when ids clash: it's versioned and
  reviewed like code.
- **A rule is "violated" only when an issue citing it survived every
  check.** The evidence checks, then the Verifier. A Reviewer that merely
  *says* a rule is violated gets `not_confirmed`. A citation of the rule
  alone never counts as proof: the violation must be shown in code.
- **Mined rules are suggestions.** Each must quote its source, and that
  quote is checked against the repository. Scopes must match real files,
  and ids must be new. A person accepts or rejects each one; rejected ids
  are never suggested again.

## Mining

`mining.py` clones the branch, then builds the Miner's brief: the file
list, README and docs, test names, and the lines where the code states
constraints ("must", "never", "only", "cannot", error messages). Files
that hold credentials (`.env`, keys) are never included. The first real
run (on `webhook-listener`, $0.008) produced 6 accurate rules, mostly
backed by the tests that enforce them. One more was discarded because its
quote didn't match the code.
