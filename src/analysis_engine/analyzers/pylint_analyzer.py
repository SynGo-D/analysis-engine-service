from .analyzer import Analyzer
from ..domain import Finding, FindingCategory
from ..workspace import Workspace


class PylintAnalyzer(Analyzer):
    """
    Pylint for Python. Command construction and Reviewdog integration are
    Phase 6.
    """

    @property
    def tool_name(self) -> str:
        return "pylint"

    @property
    def supported_languages(self) -> frozenset[str]:
        return frozenset({"python"})

    @property
    def supported_categories(self) -> frozenset[FindingCategory]:
        return frozenset({"bug", "code_smell", "style"})

    @property
    def reviewdog_format(self) -> str:
        # Reviewdog has a built-in "pylint" parser.
        return "pylint"

    def build_command(self, workspace: Workspace) -> list[str]:
        raise NotImplementedError("PylintAnalyzer.build_command is implemented in Phase 6.")

    async def analyze(self, workspace: Workspace) -> list[Finding]:
        raise NotImplementedError("PylintAnalyzer.analyze is implemented in Phase 6.")
