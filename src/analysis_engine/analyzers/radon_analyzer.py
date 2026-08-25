import json
import sys

from .analyzer import Analyzer
from .process_runner import ToolExecutionError, run_process
from ..config import settings
from ..domain import AnalysisJob, Finding, FindingCategory
from ..workspace import Workspace

# Ranks worse than these are flagged as findings; better ranks are treated
# as fine and produce no Finding — flagging every single function/file
# regardless of rank would bury genuinely concerning ones in noise.
# Radon's own scale: A (best) < B < C < D < E < F (worst).
_CC_FLAG_RANKS = {"C", "D", "E", "F"}
_MI_FLAG_RANKS = {"B", "C"}  # radon's MI rank only goes A/B/C


class RadonAnalyzer(Analyzer):
    """
    Radon for Python complexity (cc) and maintainability (mi) metrics.

    Confirmed via `reviewdog -list`: no built-in Reviewdog parser exists
    for Radon (Phase 5 already flagged this as uncertain). Its output
    also genuinely doesn't fit Reviewdog's line-diagnostic model —
    per-function complexity and per-file maintainability *scores*, not
    issues at a specific line — so this bypasses Reviewdog entirely and
    parses Radon's own JSON output directly, same treatment as Pylint.
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
        return "n/a (parsed directly, no Reviewdog built-in parser exists)"

    def build_command(self, workspace: Workspace) -> list[str]:
        # Only the cyclomatic-complexity command — analyze() runs a second
        # command (radon mi) for maintainability, since Radon has no
        # single invocation that reports both. build_command() returning
        # one list is a slight simplification of what analyze() actually
        # runs; documented here rather than silently diverging from the
        # Analyzer contract's intent.
        return [sys.executable, "-m", "radon", "cc", "-j", "."]

    async def analyze(self, workspace: Workspace, job: AnalysisJob) -> list[Finding]:
        cc_result = await run_process(
            self.build_command(workspace), cwd=workspace.path, timeout=settings.analyzer_timeout_seconds,
        )
        if cc_result.returncode != 0:
            raise ToolExecutionError(f"radon cc failed (exit {cc_result.returncode}): {cc_result.stderr.strip()}")

        mi_result = await run_process(
            [sys.executable, "-m", "radon", "mi", "-j", "."],
            cwd=workspace.path, timeout=settings.analyzer_timeout_seconds,
        )
        if mi_result.returncode != 0:
            raise ToolExecutionError(f"radon mi failed (exit {mi_result.returncode}): {mi_result.stderr.strip()}")

        findings: list[Finding] = []

        cc_data = json.loads(cc_result.stdout) if cc_result.stdout.strip() else {}
        for file_path, entries in cc_data.items():
            for entry in entries:
                if entry.get("rank") not in _CC_FLAG_RANKS:
                    continue
                findings.append(self._cc_finding(file_path, entry, job))

        mi_data = json.loads(mi_result.stdout) if mi_result.stdout.strip() else {}
        for file_path, entry in mi_data.items():
            if entry.get("rank") not in _MI_FLAG_RANKS:
                continue
            findings.append(self._mi_finding(file_path, entry, job))

        return findings

    def _cc_finding(self, file_path: str, entry: dict, job: AnalysisJob) -> Finding:
        name = entry.get("name", "<unknown>")
        complexity = entry.get("complexity")
        rank = entry.get("rank")

        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=file_path,
            line=entry.get("lineno"),
            column=entry.get("col_offset"),
            severity="warning" if rank in {"C", "D"} else "error",
            category="complexity",
            rule_id=f"cyclomatic-complexity-{rank}",
            message=f"Function '{name}' has cyclomatic complexity {complexity} (rank {rank}).",
            tool=self.tool_name,
            fingerprint="",  # Phase 7 — see EslintAnalyzer's comment for why this is deliberately deferred
        )

    def _mi_finding(self, file_path: str, entry: dict, job: AnalysisJob) -> Finding:
        mi = entry.get("mi")
        rank = entry.get("rank")

        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=file_path,
            line=None,  # file-level metric, no single line to point at
            column=None,
            severity="warning" if rank == "B" else "error",
            category="maintainability",
            rule_id=f"maintainability-index-{rank}",
            message=f"File has maintainability index {mi:.1f} (rank {rank}).",
            tool=self.tool_name,
            fingerprint="",
        )
