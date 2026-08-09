# factories

Selects the correct analyzer(s) (from `analyzers/`) based on detected
repository languages/configuration (Factory Pattern) — centralizes that
selection so the orchestrator never branches on language/tool itself.
Adding a new language/tool means implementing the analyzer contract once
and adding a case here, not touching `application/`.

Planned: Phase 5, alongside the analyzer abstraction it selects between.
