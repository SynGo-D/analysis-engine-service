from .analyzer import Analyzer
from ..domain import Finding, FindingCategory
from ..workspace import Workspace


class CppcheckAnalyzer(Analyzer):
    """
    Cppcheck for C/C++. Command construction and Reviewdog integration are
    Phase 6.
    """

    @property
    def tool_name(self) -> str:
        return "cppcheck"

    @property
    def supported_languages(self) -> frozenset[str]:
        return frozenset({"c", "cpp"})

    @property
    def supported_categories(self) -> frozenset[FindingCategory]:
        return frozenset({"bug", "vulnerability", "code_smell", "style"})

    @property
    def reviewdog_format(self) -> str:
        # Not verified: unlike eslint/pylint, I'm not confident Cppcheck
        # has a dedicated built-in Reviewdog parser name. Cppcheck can
        # emit checkstyle-style XML (--xml), which Reviewdog does support
        # natively — "checkstyle" is the likely answer, but this needs
        # confirming against Reviewdog's actual docs/--help in Phase 6.
        return "checkstyle"

    def build_command(self, workspace: Workspace) -> list[str]:
        raise NotImplementedError("CppcheckAnalyzer.build_command is implemented in Phase 6.")

    async def analyze(self, workspace: Workspace) -> list[Finding]:
        raise NotImplementedError("CppcheckAnalyzer.analyze is implemented in Phase 6.")
