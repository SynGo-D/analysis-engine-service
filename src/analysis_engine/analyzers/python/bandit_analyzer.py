import json
import logging
import time
from pathlib import Path

from ..process_runner import ToolExecutionError, run_process
from ...config import settings
from ...domain import AnalysisJob, AnalyzerRunStatus, BanditRun, Finding, Severity
from ...metrics.python.calculator import calculate_bandit_metrics
from ...workspace import Workspace

logger = logging.getLogger(__name__)

# Bandit's own exit-code convention (confirmed via direct testing against
# bandit 1.9.4): 0 = no issues found, 1 = issues found at or above the
# configured severity/confidence threshold — not a crash. Any other code
# is a genuine execution failure (bad CLI invocation, plugin error, ...).
_SUCCESS_CODES = (0, 1)

_SEVERITY_MAP: dict[str, Severity] = {"HIGH": "error", "MEDIUM": "warning", "LOW": "info"}


class BanditAnalyzer:
    """
    Runs Bandit's security scan against the workspace's Python files and
    normalizes its `-f json` output into Findings — category
    "vulnerability" throughout, reusing the existing FindingCategory
    rather than introducing a redundant "security" value.

    Not an `Analyzer` itself — see PylintAnalyzer's docstring for why;
    `PythonAnalyzer` (python_analyzer.py) aggregates this alongside
    Pylint/Radon.
    """

    tool_name = "bandit"

    def build_command(self, python_files: list[Path]) -> list[str]:
        return [
            settings.bandit_bin_path,
            "-f", "json",
            *[str(f) for f in python_files],
        ]

    async def run(self, workspace: Workspace, job: AnalysisJob, python_files: list[Path]) -> BanditRun:
        if not python_files:
            return BanditRun(status=AnalyzerRunStatus.SUCCESS)

        started = time.monotonic()
        try:
            result = await run_process(
                self.build_command(python_files),
                cwd=workspace.path,
                timeout=settings.python_analyzer_timeout_seconds,
            )
        except ToolExecutionError as error:
            logger.warning("[job:%s] bandit timed out: %s", job.job_id, error)
            return BanditRun(
                status=AnalyzerRunStatus.TIMEOUT,
                error_message=str(error),
                duration_seconds=time.monotonic() - started,
            )
        except OSError as error:
            logger.error("[job:%s] bandit failed to start: %s", job.job_id, error)
            return BanditRun(
                status=AnalyzerRunStatus.EXECUTION_ERROR,
                error_message=f"bandit failed to start: {error}",
                duration_seconds=time.monotonic() - started,
            )

        duration = time.monotonic() - started

        if result.returncode not in _SUCCESS_CODES:
            logger.error("[job:%s] bandit execution error (exit %d): %s", job.job_id, result.returncode, result.stderr.strip())
            return BanditRun(
                status=AnalyzerRunStatus.EXECUTION_ERROR,
                error_message=result.stderr.strip() or f"bandit exited with code {result.returncode}",
                duration_seconds=duration,
            )

        try:
            report = json.loads(result.stdout) if result.stdout.strip() else {"results": [], "errors": []}
        except json.JSONDecodeError as error:
            logger.error("[job:%s] bandit produced malformed JSON: %s", job.job_id, error)
            return BanditRun(
                status=AnalyzerRunStatus.EXECUTION_ERROR,
                error_message=f"Malformed bandit output: {error}",
                duration_seconds=duration,
            )

        findings = [self._to_finding(item, job, workspace) for item in report.get("results", [])]
        for parse_error in report.get("errors", []):
            logger.warning(
                "[job:%s] bandit could not fully analyze %s: %s",
                job.job_id, parse_error.get("filename"), parse_error.get("reason"),
            )
            findings.append(self._parse_error_finding(parse_error, job, workspace))

        status = AnalyzerRunStatus.COMPLETED_WITH_FINDINGS if findings else AnalyzerRunStatus.SUCCESS

        logger.info(
            "[job:%s] bandit completed in %.2fs — %d finding(s)", job.job_id, duration, len(findings)
        )

        return BanditRun(
            status=status,
            duration_seconds=duration,
            findings=findings,
            metrics=calculate_bandit_metrics(findings),
        )

    def _to_finding(self, item: dict, job: AnalysisJob, workspace: Workspace) -> Finding:
        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=self._relative_path(item["filename"], workspace),
            line=item.get("line_number"),
            column=item.get("col_offset"),
            end_column=item.get("end_col_offset"),
            severity=_SEVERITY_MAP.get(item.get("issue_severity", ""), "warning"),
            category="vulnerability",
            rule_id=item.get("test_id", "bandit"),
            message=item.get("issue_text", ""),
            tool=self.tool_name,
            fingerprint="",
            metadata={
                "testName": item.get("test_name"),
                "confidence": item.get("issue_confidence"),
                "cwe": item.get("issue_cwe"),
                "lineRange": item.get("line_range"),
            },
        )

    def _parse_error_finding(self, parse_error: dict, job: AnalysisJob, workspace: Workspace) -> Finding:
        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=self._relative_path(parse_error.get("filename", ""), workspace),
            severity="error",
            category="bug",
            rule_id="bandit-parse-error",
            message=f"Unable to analyze file: {parse_error.get('reason', 'unknown error')}",
            tool=self.tool_name,
            fingerprint="",
        )

    def _relative_path(self, raw_path: str, workspace: Workspace) -> str:
        try:
            return Path(raw_path).resolve().relative_to(workspace.path).as_posix()
        except (ValueError, OSError):
            return raw_path
