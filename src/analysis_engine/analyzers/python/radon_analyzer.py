import asyncio
import json
import logging
import time
from pathlib import Path

from ..process_runner import ToolExecutionError, run_process
from ...config import settings
from ...domain import (
    AnalysisJob,
    AnalyzerRunStatus,
    Finding,
    HalsteadEntry,
    RadonComplexityEntry,
    RadonHalsteadEntry,
    RadonMaintainabilityEntry,
    RadonRawLocEntry,
    RadonRun,
)
from ...metrics.python.calculator import (
    HIGH_COMPLEXITY_THRESHOLD,
    LOW_MAINTAINABILITY_THRESHOLD,
    calculate_maintainability_metrics,
    calculate_radon_complexity_metrics,
    calculate_halstead_metrics,
)
from ...workspace import Workspace

logger = logging.getLogger(__name__)


class _Timeout:
    """Sentinel distinguishing 'timed out' from a returned error message string."""


TIMEOUT = _Timeout()


class RadonAnalyzer:
    """
    Runs Radon's four independent measures — cyclomatic complexity (`cc`),
    maintainability index (`mi`), raw LOC (`raw`), and Halstead metrics
    (`hal`) — against the workspace's Python files.

    Unlike Pylint/Bandit, Radon doesn't produce lint-style findings on its
    own; this analyzer creates them itself for the entries that cross
    `HIGH_COMPLEXITY_THRESHOLD`/`LOW_MAINTAINABILITY_THRESHOLD`, while
    still preserving every raw entry (complexity/maintainability/halstead/
    raw_loc on RadonRun) — see domain/python_metrics.py.

    `raw` (LOC) and `hal` (Halstead) are best-effort: Radon's own exit
    code is always 0 regardless of per-file errors (confirmed via direct
    testing — a syntax error or missing file surfaces as a `{"error":
    ...}` JSON entry, not a nonzero exit), and Halstead is explicitly
    optional per spec ("where supported"), so a Halstead failure alone
    doesn't fail the whole Radon run.
    """

    tool_name = "radon"

    async def run(self, workspace: Workspace, job: AnalysisJob, python_files: list[Path]) -> RadonRun:
        if not python_files:
            return RadonRun(status=AnalyzerRunStatus.SUCCESS)

        started = time.monotonic()
        file_args = [str(f) for f in python_files]

        cc_task = self._run_json_command(["cc", "--json", *file_args], workspace, job, "cc")
        mi_task = self._run_json_command(["mi", "--json", *file_args], workspace, job, "mi")
        raw_task = self._run_json_command(["raw", "--json", *file_args], workspace, job, "raw")
        hal_task = self._run_json_command(["hal", "--json", *file_args], workspace, job, "hal")

        cc_raw, mi_raw, raw_raw, hal_raw = await asyncio.gather(cc_task, mi_task, raw_task, hal_task)
        duration = time.monotonic() - started

        # cc/mi/raw are required — Radon timing out or erroring on any of
        # them means this run can't produce trustworthy complexity/
        # maintainability/LOC data.
        for label, outcome in (("cc", cc_raw), ("mi", mi_raw), ("raw", raw_raw)):
            if outcome is TIMEOUT:
                return RadonRun(
                    status=AnalyzerRunStatus.TIMEOUT,
                    error_message=f"radon {label} timed out",
                    duration_seconds=duration,
                )
            if isinstance(outcome, str):  # error message string
                return RadonRun(
                    status=AnalyzerRunStatus.EXECUTION_ERROR,
                    error_message=f"radon {label}: {outcome}",
                    duration_seconds=duration,
                )

        complexity_entries, complexity_findings = self._parse_complexity(cc_raw, job, workspace)
        maintainability_entries, maintainability_findings = self._parse_maintainability(mi_raw, job, workspace)
        raw_loc_entries = self._parse_raw_loc(raw_raw, job, workspace)

        halstead_entries: list[RadonHalsteadEntry] = []
        if isinstance(hal_raw, dict):
            halstead_entries = self._parse_halstead(hal_raw, workspace)
        elif hal_raw is not None:
            logger.warning("[job:%s] radon hal unavailable, continuing without Halstead data: %s", job.job_id, hal_raw)

        findings = complexity_findings + maintainability_findings
        status = AnalyzerRunStatus.COMPLETED_WITH_FINDINGS if findings else AnalyzerRunStatus.SUCCESS

        logger.info(
            "[job:%s] radon completed in %.2fs — %d finding(s), %d complexity entries",
            job.job_id, duration, len(findings), len(complexity_entries),
        )

        return RadonRun(
            status=status,
            duration_seconds=duration,
            findings=findings,
            complexity=complexity_entries,
            maintainability=maintainability_entries,
            halstead=halstead_entries,
            raw_loc=raw_loc_entries,
            complexity_metrics=calculate_radon_complexity_metrics(complexity_entries),
            maintainability_metrics=calculate_maintainability_metrics(maintainability_entries),
            halstead_metrics=calculate_halstead_metrics(halstead_entries),
        )

    async def _run_json_command(
        self, args: list[str], workspace: Workspace, job: AnalysisJob, label: str
    ):
        """Returns parsed JSON (dict/list) on success, TIMEOUT sentinel on timeout, or an error message string."""
        try:
            result = await run_process(
                [settings.radon_bin_path, *args],
                cwd=workspace.path,
                timeout=settings.python_analyzer_timeout_seconds,
            )
        except ToolExecutionError as error:
            logger.warning("[job:%s] radon %s timed out: %s", job.job_id, label, error)
            return TIMEOUT
        except OSError as error:
            logger.error("[job:%s] radon %s failed to start: %s", job.job_id, label, error)
            return f"failed to start ({error})"

        if not result.stdout.strip():
            return {}

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            return f"malformed JSON ({error})"

    def _parse_complexity(
        self, raw: dict, job: AnalysisJob, workspace: Workspace
    ) -> tuple[list[RadonComplexityEntry], list[Finding]]:
        entries: list[RadonComplexityEntry] = []
        findings: list[Finding] = []

        for file_path, blocks in raw.items():
            relative_path = self._relative_path(file_path, workspace)

            # A per-file error replaces the whole per-file value with a
            # single {"error": ...} dict (confirmed via direct testing),
            # not a list containing one error block — must be checked
            # before treating `blocks` as a list of complexity blocks.
            if isinstance(blocks, dict) and "error" in blocks:
                findings.append(self._parse_error_finding(relative_path, blocks["error"], job, "radon-cc"))
                continue

            for block in self._flatten_complexity_blocks(blocks):
                entry = RadonComplexityEntry(
                    file=relative_path,
                    object=block["name"],
                    type=block["type"],
                    line=block["lineno"],
                    end_line=block["endline"],
                    complexity=block["complexity"],
                    rank=block["rank"],
                )
                entries.append(entry)

                if entry.complexity >= HIGH_COMPLEXITY_THRESHOLD:
                    findings.append(Finding(
                        repository=job.repository,
                        pull_request_number=job.pull_request_number,
                        commit_sha=job.commit_sha,
                        file_path=relative_path,
                        line=entry.line,
                        end_line=entry.end_line,
                        severity="error" if entry.complexity >= 21 else "warning",
                        category="complexity",
                        rule_id="radon-cyclomatic-complexity",
                        message=(
                            f"{entry.type.capitalize()} '{entry.object}' has a cyclomatic complexity "
                            f"of {entry.complexity} (rank {entry.rank})."
                        ),
                        tool=self.tool_name,
                        fingerprint="",
                        metadata={"rank": entry.rank, "objectType": entry.type},
                    ))

        return entries, findings

    def _flatten_complexity_blocks(self, blocks) -> list[dict]:
        """Radon nests closures (nested functions) inside their parent block — flatten one level of that."""
        flat: list[dict] = []
        for block in blocks:
            if "error" in block:
                flat.append(block)
                continue
            flat.append(block)
            flat.extend(block.get("closures", []))
        return flat

    def _parse_maintainability(
        self, raw: dict, job: AnalysisJob, workspace: Workspace
    ) -> tuple[list[RadonMaintainabilityEntry], list[Finding]]:
        entries: list[RadonMaintainabilityEntry] = []
        findings: list[Finding] = []

        for file_path, value in raw.items():
            relative_path = self._relative_path(file_path, workspace)
            if "error" in value:
                findings.append(self._parse_error_finding(relative_path, value["error"], job, "radon-mi"))
                continue

            entry = RadonMaintainabilityEntry(file=relative_path, mi=value["mi"], rank=value["rank"])
            entries.append(entry)

            if entry.mi < LOW_MAINTAINABILITY_THRESHOLD:
                findings.append(Finding(
                    repository=job.repository,
                    pull_request_number=job.pull_request_number,
                    commit_sha=job.commit_sha,
                    file_path=relative_path,
                    severity="error" if entry.mi < 50 else "warning",
                    category="maintainability",
                    rule_id="radon-maintainability-index",
                    message=f"Maintainability Index is {entry.mi:.1f} (rank {entry.rank}).",
                    tool=self.tool_name,
                    fingerprint="",
                    metadata={"rank": entry.rank},
                ))

        return entries, findings

    def _parse_raw_loc(self, raw: dict, job: AnalysisJob, workspace: Workspace) -> list[RadonRawLocEntry]:
        entries: list[RadonRawLocEntry] = []
        for file_path, value in raw.items():
            if "error" in value:
                continue
            entries.append(RadonRawLocEntry(
                file=self._relative_path(file_path, workspace),
                loc=value["loc"], lloc=value["lloc"], sloc=value["sloc"],
                comments=value["comments"], multi=value["multi"],
                blank=value["blank"], single_comments=value["single_comments"],
            ))
        return entries

    def _parse_halstead(self, raw: dict, workspace: Workspace) -> list[RadonHalsteadEntry]:
        entries: list[RadonHalsteadEntry] = []
        for file_path, value in raw.items():
            if "error" in value or "total" not in value:
                continue
            entries.append(RadonHalsteadEntry(
                file=self._relative_path(file_path, workspace),
                total=HalsteadEntry(**value["total"]),
                functions={name: HalsteadEntry(**data) for name, data in value.get("functions", {}).items()},
            ))
        return entries

    def _parse_error_finding(self, relative_path: str, error: str, job: AnalysisJob, rule_id: str) -> Finding:
        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=relative_path,
            severity="error",
            category="bug",
            rule_id=rule_id,
            message=f"Unable to analyze file: {error}",
            tool=self.tool_name,
            fingerprint="",
        )

    def _relative_path(self, raw_path: str, workspace: Workspace) -> str:
        try:
            return Path(raw_path).resolve().relative_to(workspace.path).as_posix()
        except (ValueError, OSError):
            return raw_path
