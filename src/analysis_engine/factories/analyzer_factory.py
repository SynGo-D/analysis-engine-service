from ..analyzers import Analyzer, EslintAnalyzer, JavaAnalyzer, PythonAnalyzer

# Adding a new tool/language means implementing the Analyzer interface
# once and adding it here — nothing else in this file, or the
# orchestrator, needs to change.
_ALL_ANALYZERS: tuple[type[Analyzer], ...] = (
    EslintAnalyzer,
    JavaAnalyzer,
    PythonAnalyzer,
)


class AnalyzerFactory:
    """
    Selects the analyzer(s) applicable to a set of detected languages
    (Factory Pattern). Multiple analyzers can apply to the same language
    in principle (kept list-shaped, not single-instance-returning, for
    that reason) — currently one analyzer per language: EslintAnalyzer
    for JS/TS, JavaAnalyzer (PMD) for Java, PythonAnalyzer (itself a
    composite over Pylint/Radon/Bandit — see
    analyzers/python/python_analyzer.py) for Python.
    """

    def create_for_languages(self, languages: frozenset[str]) -> list[Analyzer]:
        analyzers: list[Analyzer] = []

        for analyzer_cls in _ALL_ANALYZERS:
            instance = analyzer_cls()
            if any(instance.supports(language) for language in languages):
                analyzers.append(instance)

        return analyzers
