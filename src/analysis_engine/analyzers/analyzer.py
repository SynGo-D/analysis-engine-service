from abc import ABC, abstractmethod

from ..domain import AnalysisJob, Finding, FindingCategory
from ..workspace import Workspace


class Analyzer(ABC):
    """
    Strategy Pattern: one implementation per static-analysis tool. The
    factory and orchestrator depend only on this interface — never on a
    specific tool's command line, output format, or normalization logic.
    Currently implemented once, by `EslintAnalyzer`; kept as an interface
    (rather than inlining ESLint-specific calls into the orchestrator) so
    a second JS/TS-capable tool could be added later without touching
    `application/orchestrator.py`.
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
        """The kinds of findings this tool can realistically produce."""

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
    async def analyze(self, workspace: Workspace, job: AnalysisJob) -> list[Finding]:
        """
        Runs build_command()'s tool and normalizes its own output into
        Finding objects.

        `job` is required, not optional: every Finding produced here must
        carry repository/pull_request_number/commit_sha, and Workspace
        only carries `job_id` — it has no reason to duplicate the rest of
        AnalysisJob's fields just for this.
        """
