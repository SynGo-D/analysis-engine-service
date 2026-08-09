from abc import ABC, abstractmethod

from ..domain import Finding, FindingCategory
from ..workspace import Workspace


class Analyzer(ABC):
    """
    Strategy Pattern: one implementation per static-analysis tool. The
    factory and orchestrator depend only on this interface — never on a
    specific tool's command line, output format, or normalization logic.

    Reviewdog sits between build_command()'s tool invocation and
    analyze()'s return value: analyzers are responsible for feeding their
    tool's raw output to Reviewdog and turning Reviewdog's aggregated
    diagnostics into Finding objects, not for reimplementing result
    aggregation themselves.
    """

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """Short identifier used in Finding.tool and log messages (e.g. "eslint")."""

    @property
    @abstractmethod
    def supported_languages(self) -> frozenset[str]:
        """Languages this analyzer runs against — matches the values language_detector.py produces."""

    @property
    @abstractmethod
    def supported_categories(self) -> frozenset[FindingCategory]:
        """The kinds of findings this tool can realistically produce (e.g. Radon: complexity/maintainability, not bugs)."""

    @property
    @abstractmethod
    def reviewdog_format(self) -> str:
        """The reviewdog -f/-name value describing this tool's raw output format."""

    def supports(self, language: str) -> bool:
        return language in self.supported_languages

    @abstractmethod
    def build_command(self, workspace: Workspace) -> list[str]:
        """
        The tool's own command line to run against `workspace` — an
        argument list, never a shell string (same injection-safety
        reasoning as workspace/git_client.py: this list is passed to
        asyncio.create_subprocess_exec, never shell=True).
        """

    @abstractmethod
    async def analyze(self, workspace: Workspace) -> list[Finding]:
        """
        Runs build_command()'s tool, pipes its output through Reviewdog
        (using reviewdog_format), and normalizes Reviewdog's aggregated
        diagnostics into Finding objects. Implemented per-tool in Phase 6
        — different tools need different Reviewdog invocation shapes.
        """
