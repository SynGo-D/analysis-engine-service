import json
import sys

from .analyzer import Analyzer
from .process_runner import ToolExecutionError, run_process
from ..config import settings
from ..domain import AnalysisJob, Finding, FindingCategory, Severity
from ..workspace import Workspace

# Confirmed via `reviewdog -list`: no built-in Reviewdog parser exists for
# Pylint (unlike ESLint) — Phase 5's assumption that one did was wrong.
# Pylint's own JSON output is well-structured and easy to parse directly,
# so this bypasses Reviewdog entirely rather than fighting a custom
# -efm pattern for uncertain benefit.
_TYPE_TO_SEVERITY: dict[str, Severity] = {
    "fatal": "error", "error": "error",
    "warning": "warning",
    "convention": "info", "refactor": "info",
}

_TYPE_TO_CATEGORY: dict[str, FindingCategory] = {
    "fatal": "bug", "error": "bug", "warning": "bug",
    "convention": "style",
    "refactor": "code_smell",
}


class PylintAnalyzer(Analyzer):
    """Pylint for Python — see module docstring above for why this bypasses Reviewdog's -f parsing."""

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
        # Not actually used — analyze() bypasses Reviewdog for this tool.
        # Kept as an honest label rather than a real "-f" value.
        return "n/a (parsed directly, no Reviewdog built-in parser exists)"

    def build_command(self, workspace: Workspace) -> list[str]:
        # sys.executable, not a bare "pylint" on PATH: guarantees this
        # runs analysis-engine's own installed Pylint (pyproject.toml),
        # not some other interpreter's, regardless of PATH.
        return [sys.executable, "-m", "pylint", "--output-format=json", "."]

    async def analyze(self, workspace: Workspace, job: AnalysisJob) -> list[Finding]:
        result = await run_process(
            self.build_command(workspace), cwd=workspace.path, timeout=settings.analyzer_timeout_seconds,
        )

        # Pylint's exit code is a bitmask (1=fatal, 2=error, 4=warning,
        # 8=refactor, 16=convention, 32=usage-error) — confirmed by direct
        # testing (exit 22 = 16+4+2, meaning convention+warning+error
        # findings, not a crash). Bits 1 (fatal) and 32 (usage error) are
        # the only ones that mean this run itself failed.
        if result.returncode & 1 or result.returncode & 32:
            raise ToolExecutionError(
                f"pylint exited with a fatal/usage error (code {result.returncode}): {result.stderr.strip()}"
            )

        if not result.stdout.strip():
            return []

        raw_findings = json.loads(result.stdout)
        return [self._to_finding(item, job) for item in raw_findings]

    def _to_finding(self, item: dict, job: AnalysisJob) -> Finding:
        pylint_type = item.get("type", "warning")

        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=item["path"],
            line=item.get("line"),
            column=item.get("column"),
            severity=_TYPE_TO_SEVERITY.get(pylint_type, "warning"),
            # Same caveat as EslintAnalyzer: a coarse type->category
            # mapping, not a per-rule one — real refinement is Phase 7's.
            category=_TYPE_TO_CATEGORY.get(pylint_type, "code_smell"),
            rule_id=item.get("symbol", item.get("message-id", "pylint")),
            message=item["message"],
            tool=self.tool_name,
            fingerprint="",  # Phase 7 — see EslintAnalyzer's comment for why this is deliberately deferred
        )
