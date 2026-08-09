from .analyzer import Analyzer
from ..domain import Finding, FindingCategory
from ..workspace import Workspace


class EslintAnalyzer(Analyzer):
    """
    ESLint for JavaScript/TypeScript. Command construction and Reviewdog
    integration are Phase 6 — this phase establishes the contract
    (languages/categories/format) so the factory can already select this
    analyzer correctly.
    """

    @property
    def tool_name(self) -> str:
        return "eslint"

    @property
    def supported_languages(self) -> frozenset[str]:
        return frozenset({"javascript", "typescript"})

    @property
    def supported_categories(self) -> frozenset[FindingCategory]:
        return frozenset({"bug", "code_smell", "style"})

    @property
    def reviewdog_format(self) -> str:
        # Reviewdog has a built-in "eslint" parser for ESLint's own JSON
        # output (-f json) directly — no custom errorformat needed.
        return "eslint"

    def build_command(self, workspace: Workspace) -> list[str]:
        raise NotImplementedError("EslintAnalyzer.build_command is implemented in Phase 6.")

    async def analyze(self, workspace: Workspace) -> list[Finding]:
        raise NotImplementedError("EslintAnalyzer.analyze is implemented in Phase 6.")
