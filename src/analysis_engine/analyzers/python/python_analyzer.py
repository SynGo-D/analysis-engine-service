import asyncio
import logging
from pathlib import Path

from .bandit_analyzer import BanditAnalyzer
from .pylint_analyzer import PylintAnalyzer
from .radon_analyzer import RadonAnalyzer
from ..analyzer import Analyzer
from ...domain import AnalysisJob, AnalyzerRunStatus, Finding, FindingCategory, PythonAnalysisResult
from ...metrics.python.calculator import (
    calculate_bandit_metrics,
    calculate_pylint_metrics,
    calculate_python_loc,
    calculate_python_metrics,
    calculate_radon_complexity_metrics,
)
from ...workspace import Workspace

logger = logging.getLogger(__name__)

_IGNORED_DIR_NAMES = {".git", ".venv", "venv", "env", "__pycache__", "node_modules", "dist", "build"}


def discover_python_files(workspace_path: Path) -> list[Path]:
    """
    Finds every `.py` file once, so Pylint/Radon/Bandit each analyze
    exactly the same file set instead of three independent directory
    walks — reused by PythonAnalyzer.analyze() and by
    factories/language_detector.py's ignored-directory convention (kept
    in sync by hand since the two live at different layers: language
    detection just needs to know Python *exists*, this needs the actual
    file list to hand to three subprocesses).
    """
    return sorted(
        path
        for path in workspace_path.rglob("*.py")
        if not any(part in _IGNORED_DIR_NAMES for part in path.parts)
    )


class PythonAnalyzer(Analyzer):
    """
    Composite analyzer (Adapter over three tools + Strategy for the
    orchestrator) — implements the standard `Analyzer` interface so
    `AnalysisOrchestrator`/`AnalyzerFactory` treat Python exactly like
    ESLint: one entry in the analyzer list, run via the same
    `asyncio.gather`, subject to the same partial-failure handling.

    Internally runs Pylint, Radon, and Bandit concurrently (each fully
    independent — none write to the workspace) and tracks each one's own
    execution status separately (see domain/analyzer_status.py), so one
    sub-tool timing out doesn't discard the other two's real results —
    the "failure isolation" requirement. That richer per-tool detail
    (raw findings, raw Radon metrics, per-tool status) doesn't fit the
    plain `list[Finding]` the `Analyzer` interface returns, so it's
    exposed separately via `self.last_result` (a `PythonAnalysisResult`,
    populated as a side effect of `analyze()`) — `AnalysisOrchestrator`
    reads that back after running all analyzers to populate
    `AnalysisResult.python`, without the `Analyzer` interface itself, or
    `EslintAnalyzer`, needing to change.
    """

    def __init__(self):
        self._pylint = PylintAnalyzer()
        self._radon = RadonAnalyzer()
        self._bandit = BanditAnalyzer()
        self.last_result: PythonAnalysisResult | None = None

    @property
    def tool_name(self) -> str:
        return "python"

    @property
    def supported_languages(self) -> frozenset[str]:
        return frozenset({"python"})

    @property
    def supported_categories(self) -> frozenset[FindingCategory]:
        return frozenset({"bug", "vulnerability", "code_smell", "style", "complexity", "maintainability"})

    def build_command(self, workspace: Workspace) -> list[str]:
        # No single command — this composite dispatches to three
        # independent tool invocations, each built by its own adapter
        # (PylintAnalyzer.build_command, BanditAnalyzer.build_command,
        # and RadonAnalyzer's four internal radon subcommands).
        return []

    async def analyze(self, workspace: Workspace, job: AnalysisJob) -> list[Finding]:
        python_files = discover_python_files(workspace.path)
        logger.info("[job:%s] python analysis started — %d file(s)", job.job_id, len(python_files))

        pylint_run, radon_run, bandit_run = await asyncio.gather(
            self._pylint.run(workspace, job, python_files),
            self._radon.run(workspace, job, python_files),
            self._bandit.run(workspace, job, python_files),
        )

        loc = calculate_python_loc(radon_run.raw_loc)
        kloc = loc.kloc

        # Density-dependent figures were computed with kloc=0.0 inside
        # each adapter (which runs before Radon's LOC is known) — cheap
        # to recompute now that the real kloc is available, since these
        # are pure functions over already-collected findings/entries, not
        # new subprocess calls.
        pylint_run = pylint_run.model_copy(update={"metrics": calculate_pylint_metrics(pylint_run.findings, kloc)})
        bandit_run = bandit_run.model_copy(update={"metrics": calculate_bandit_metrics(bandit_run.findings, kloc)})
        radon_run = radon_run.model_copy(
            update={"complexity_metrics": calculate_radon_complexity_metrics(radon_run.complexity, kloc)}
        )

        python_metrics = calculate_python_metrics(
            loc=loc,
            pylint_metrics=pylint_run.metrics,
            complexity_metrics=radon_run.complexity_metrics,
            maintainability_metrics=radon_run.maintainability_metrics,
            halstead_metrics=radon_run.halstead_metrics,
            bandit_metrics=bandit_run.metrics,
        )

        self.last_result = PythonAnalysisResult(
            pylint=pylint_run, radon=radon_run, bandit=bandit_run, metrics=python_metrics,
        )

        for name, run in (("pylint", pylint_run), ("radon", radon_run), ("bandit", bandit_run)):
            logger.info(
                "[job:%s] python/%s: status=%s duration=%.2fs findings=%d",
                job.job_id, name, run.status.value, run.duration_seconds, len(run.findings),
            )
            if run.status == AnalyzerRunStatus.EXECUTION_ERROR:
                logger.error("[job:%s] python/%s execution error: %s", job.job_id, name, run.error_message)
            elif run.status == AnalyzerRunStatus.TIMEOUT:
                logger.warning("[job:%s] python/%s timed out", job.job_id, name)

        statuses = {pylint_run.status, radon_run.status, bandit_run.status}
        if statuses <= {AnalyzerRunStatus.EXECUTION_ERROR, AnalyzerRunStatus.TIMEOUT} and python_files:
            # Mirrors AnalysisOrchestrator's own "every analyzer failed"
            # policy, one level down: if every Python sub-tool failed,
            # this composite analyzer itself failed — the orchestrator's
            # existing partial-failure handling (analyzers/README.md)
            # takes it from there.
            raise RuntimeError(
                f"All Python analyzers failed: pylint={pylint_run.status.value}, "
                f"radon={radon_run.status.value}, bandit={bandit_run.status.value}"
            )

        return pylint_run.findings + radon_run.findings + bandit_run.findings
