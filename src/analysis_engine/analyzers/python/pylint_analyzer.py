import json
import logging
import time
from pathlib import Path

from ..process_runner import ToolExecutionError, run_process
from ...config import settings
from ...domain import AnalysisJob, AnalyzerRunStatus, Finding, FindingCategory, PylintRun, Severity
from ...metrics.python.calculator import calculate_pylint_metrics
from ...workspace import Workspace

logger = logging.getLogger(__name__)

# Pylint's own bitmask exit-code convention (see pylintrc / --help):
# 1=fatal, 2=error, 4=warning, 8=refactor, 16=convention, 32=usage error.
# Only bit 32 means "the tool itself failed" — every other combination
# (including 1/fatal, e.g. a syntax error in the analyzed file) still
# produced valid JSON describing what it found, confirmed by direct
# testing against pylint 4.0.8 rather than assumed from older docs.
_USAGE_ERROR_BIT = 32

_TYPE_TO_CATEGORY: dict[str, FindingCategory] = {
    "fatal": "bug",
    "error": "bug",
    "warning": "code_smell",
    "refactor": "code_smell",
    "convention": "style",
}
_TYPE_TO_SEVERITY: dict[str, Severity] = {
    "fatal": "error",
    "error": "error",
    "warning": "warning",
    "refactor": "warning",
    "convention": "info",
}


class PylintAnalyzer:
    """
    Runs Pylint against the workspace's Python files and normalizes its
    `--output-format=json` output into Findings — never reduced to counts
    alone, per the raw-output-preservation requirement (see
    metrics/python/calculator.py for where the aggregate PylintMetrics
    gets computed instead, kept separate from these Findings).

    Not an `Analyzer` (analyzers/analyzer.py) itself: that interface
    returns a flat `list[Finding]`, but this needs to report its own
    execution status distinctly (success / found issues / execution
    error / timeout — see domain/analyzer_status.py) so one Python
    sub-tool timing out doesn't get misread as the whole analysis
    failing. `PythonAnalyzer` (python_analyzer.py) is the one that
    implements `Analyzer` and aggregates this alongside Radon/Bandit.
    """

    tool_name = "pylint"

    def build_command(self, python_files: list[Path]) -> list[str]:
        return [
            settings.pylint_bin_path,
            "--output-format=json",
            *[str(f) for f in python_files],
        ]

    async def run(self, workspace: Workspace, job: AnalysisJob, python_files: list[Path]) -> PylintRun:
        if not python_files:
            return PylintRun(status=AnalyzerRunStatus.SUCCESS)

        started = time.monotonic()
        try:
            result = await run_process(
                self.build_command(python_files),
                cwd=workspace.path,
                timeout=settings.python_analyzer_timeout_seconds,
            )
        except ToolExecutionError as error:
            logger.warning("[job:%s] pylint timed out: %s", job.job_id, error)
            return PylintRun(
                status=AnalyzerRunStatus.TIMEOUT,
                error_message=str(error),
                duration_seconds=time.monotonic() - started,
            )
        except OSError as error:
            # Covers a missing/unexecutable pylint binary (FileNotFoundError,
            # PermissionError, ...) — asyncio.create_subprocess_exec raises
            # these directly, before run_process's own timeout handling
            # ever applies, so they're not a ToolExecutionError.
            logger.error("[job:%s] pylint failed to start: %s", job.job_id, error)
            return PylintRun(
                status=AnalyzerRunStatus.EXECUTION_ERROR,
                error_message=f"pylint failed to start: {error}",
                duration_seconds=time.monotonic() - started,
            )

        duration = time.monotonic() - started

        if (result.returncode & _USAGE_ERROR_BIT) and not result.stdout.strip():
            logger.error("[job:%s] pylint usage error: %s", job.job_id, result.stderr.strip())
            return PylintRun(
                status=AnalyzerRunStatus.EXECUTION_ERROR,
                error_message=result.stderr.strip() or f"pylint exited with code {result.returncode}",
                duration_seconds=duration,
            )

        try:
            raw_messages = json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError as error:
            logger.error("[job:%s] pylint produced malformed JSON: %s", job.job_id, error)
            return PylintRun(
                status=AnalyzerRunStatus.EXECUTION_ERROR,
                error_message=f"Malformed pylint output: {error}",
                duration_seconds=duration,
            )

        findings = [self._to_finding(message, job, workspace) for message in raw_messages]
        status = AnalyzerRunStatus.COMPLETED_WITH_FINDINGS if findings else AnalyzerRunStatus.SUCCESS

        logger.info(
            "[job:%s] pylint completed in %.2fs — %d finding(s)", job.job_id, duration, len(findings)
        )

        return PylintRun(
            status=status,
            duration_seconds=duration,
            findings=findings,
            metrics=calculate_pylint_metrics(findings),
        )

    def _to_finding(self, message: dict, job: AnalysisJob, workspace: Workspace) -> Finding:
        pylint_type = message.get("type", "warning")
        file_path = self._relative_path(message.get("path", ""), workspace)

        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=file_path,
            line=message.get("line"),
            column=message.get("column"),
            end_line=message.get("endLine"),
            end_column=message.get("endColumn"),
            severity=_TYPE_TO_SEVERITY.get(pylint_type, "warning"),
            category=_TYPE_TO_CATEGORY.get(pylint_type, "code_smell"),
            rule_id=message.get("symbol") or message.get("message-id", "pylint"),
            message=message.get("message", ""),
            tool=self.tool_name,
            fingerprint="",
            metadata={
                "pylintType": pylint_type,
                "messageId": message.get("message-id"),
                "module": message.get("module"),
                "obj": message.get("obj"),
            },
        )

    def _relative_path(self, raw_path: str, workspace: Workspace) -> str:
        try:
            return Path(raw_path).resolve().relative_to(workspace.path).as_posix()
        except (ValueError, OSError):
            return raw_path
