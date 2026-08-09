from .analyzer import Analyzer
from ..domain import Finding, FindingCategory
from ..workspace import Workspace


class RadonAnalyzer(Analyzer):
    """
    Radon for Python complexity/maintainability metrics. Command
    construction and Reviewdog integration are Phase 6.

    Worth flagging now: Radon doesn't fit the traditional "linter with
    line-level diagnostics" shape the way ESLint/Pylint/Cppcheck do — its
    output is per-function/per-file complexity and maintainability
    *scores*, not line-anchored issues (matching Finding.line being
    optional, from Phase 2). It's unclear yet whether Reviewdog's
    errorformat matching is even the right mechanism for Radon's output,
    or whether Phase 6 ends up normalizing Radon's JSON output directly
    without going through Reviewdog's diagnostic aggregation the same way
    the other three tools do.
    """

    @property
    def tool_name(self) -> str:
        return "radon"

    @property
    def supported_languages(self) -> frozenset[str]:
        return frozenset({"python"})

    @property
    def supported_categories(self) -> frozenset[FindingCategory]:
        return frozenset({"complexity", "maintainability"})

    @property
    def reviewdog_format(self) -> str:
        # Best-guess placeholder, not verified: Radon has no dedicated
        # built-in Reviewdog parser (unlike eslint/pylint above), since
        # its output isn't standard line-diagnostic lint format. "rdjson"
        # is Reviewdog's own generic JSON diagnostic format — the likely
        # answer, but this needs confirming against Reviewdog's actual
        # docs/--help in Phase 6, not asserted as verified here.
        return "rdjson"

    def build_command(self, workspace: Workspace) -> list[str]:
        raise NotImplementedError("RadonAnalyzer.build_command is implemented in Phase 6.")

    async def analyze(self, workspace: Workspace) -> list[Finding]:
        raise NotImplementedError("RadonAnalyzer.analyze is implemented in Phase 6.")
