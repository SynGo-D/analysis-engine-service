from ..analyzers import Analyzer, EslintAnalyzer

# Single-tool by design: this service is a dedicated ESLint (JS/TS)
# analyzer. Adding a future tool/language back means implementing the
# Analyzer interface once and adding it here — nothing else in this file,
# or the orchestrator, needs to change.
_ALL_ANALYZERS: tuple[type[Analyzer], ...] = (
    EslintAnalyzer,
)


class AnalyzerFactory:
    """
    Selects the analyzer(s) applicable to a set of detected languages
    (Factory Pattern). Still returns a list (not a single instance) so a
    future second JS/TS-capable tool can be added without changing this
    method's contract.
    """

    def create_for_languages(self, languages: frozenset[str]) -> list[Analyzer]:
        analyzers: list[Analyzer] = []

        for analyzer_cls in _ALL_ANALYZERS:
            instance = analyzer_cls()
            if any(instance.supports(language) for language in languages):
                analyzers.append(instance)

        return analyzers
