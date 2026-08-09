from ..analyzers import Analyzer, EslintAnalyzer, PylintAnalyzer, RadonAnalyzer, CppcheckAnalyzer

# Adding a new tool/language means implementing the Analyzer interface
# once and adding it here — nothing else in this file, or the
# orchestrator, needs to change.
_ALL_ANALYZERS: tuple[type[Analyzer], ...] = (
    EslintAnalyzer,
    PylintAnalyzer,
    RadonAnalyzer,
    CppcheckAnalyzer,
)


class AnalyzerFactory:
    """
    Selects the analyzer(s) applicable to a set of detected languages
    (Factory Pattern). Multiple analyzers can apply to the same language
    — Pylint and Radon both target Python — so this returns every
    applicable analyzer, not just one; the orchestrator runs all of them.
    """

    def create_for_languages(self, languages: frozenset[str]) -> list[Analyzer]:
        analyzers: list[Analyzer] = []

        for analyzer_cls in _ALL_ANALYZERS:
            instance = analyzer_cls()
            if any(instance.supports(language) for language in languages):
                analyzers.append(instance)

        return analyzers
