# analyzers

Tool adapters (Adapter Pattern) — one per static-analysis tool (ESLint,
Pylint, Radon, Cppcheck), each implementing a shared analyzer contract:
what command it executes, how Reviewdog receives its output, what output
format is expected, how findings are normalized, and what capabilities it
supports (languages, categories).

Reviewdog is the review/diagnostic aggregation and reporting layer sitting
behind these adapters — this service owns orchestration and result
processing; Reviewdog is not where business logic lives.

Planned: Phase 5 (the analyzer interface/contract — Strategy Pattern for
language-specific pipelines), Phase 6 (concrete ESLint/Pylint/Radon/
Cppcheck adapters + Reviewdog integration).
