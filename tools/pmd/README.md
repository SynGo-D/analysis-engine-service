# PMD — the Java toolchain

This service's own copy of PMD, never the analyzed repository's.

## Why PMD, and not SpotBugs or Checkstyle

PMD reads **source**. SpotBugs reads **bytecode**, which would mean
compiling every repository under review with its own Maven or Gradle
plugins — arbitrary code execution on the analysis host, which is exactly
what `workspace/README.md` forbids. Checkstyle reports formatting
conventions, and a review that mixes brace placement in with real defects
teaches people to skim past all of it.

## Why the ruleset is curated

`ruleset.xml` is a hand-picked list, not a whole category. Running PMD's
full `bestpractices`, `design` and `errorprone` categories against a
14-file Spring service produced ten violations, of which six were
`LawOfDemeter` and `GuardLogStatement` noise and none were defects.

Four rules in it are **load-bearing**: `CyclomaticComplexity`,
`CognitiveComplexity`, `NcssCount` and the `Unused*` family. The
dashboard's Complexity, Code Size and Unused Code panels are derived from
their violation messages by `metrics/calculator.py`, exactly as the
JavaScript ones are derived from ESLint's `complexity` and `max-lines`
rules. Removing one empties a panel.

That also means **PMD's version is pinned** (`Dockerfile`, `PMD_VERSION`):
those metrics are parsed out of PMD's exact wording, so an upgrade that
rephrases a rule would silently blank a panel rather than fail loudly.
`tests/test_metrics_calculator.py` holds the wording verbatim, and is what
would catch it.

## Installing locally

The image downloads its own pinned copy. For running the analyzer on your
machine:

```bash
VERSION=7.18.0
curl -fsSL -o /tmp/pmd.zip \
  "https://github.com/pmd/pmd/releases/download/pmd_releases%2F${VERSION}/pmd-dist-${VERSION}-bin.zip"
unzip -q /tmp/pmd.zip -d /tmp/pmd
mv "/tmp/pmd/pmd-bin-${VERSION}" tools/pmd/pmd-bin
```

`pmd-bin/` is git-ignored, like `tools/eslint/node_modules/`. PMD needs a
JRE on `PATH`.

## Thresholds

Left at PMD's defaults. They are what the reported numbers are measured
against, and a repository that never crosses them correctly reports zero
violations. Note that the complexity **average** and **maximum** on the
dashboard are computed across methods that exceeded the threshold — a
codebase with none leaves them blank rather than showing a figure
averaged over nothing.
