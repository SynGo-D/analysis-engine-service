import asyncio
import logging
from datetime import datetime, timezone

from .finding_normalizer import FindingNormalizer
from ..analyzers import Analyzer
from ..domain import AnalysisJob, AnalysisResult, Finding
from ..factories import AnalyzerFactory, detect_languages
from ..metrics import calculate_file_statistics, calculate_metrics, calculate_rule_statistics, scan_js_ts_files
from ..workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class AnalysisOrchestrator:
    """
    Coordinates the full analysis pipeline for one job:

        validate job -> obtain repository -> create workspace
          -> checkout commit -> detect languages -> select analyzers
          -> run ESLint -> normalize findings -> calculate metrics
          -> return result (persistence/publish happen in the caller)

    This is the only place that sequence is encoded (Pipeline/Command
    Pattern) — the consumer just calls `run()` and turns the outcome into
    an ack/nack.
    """

    def __init__(
        self,
        workspace_manager: WorkspaceManager | None = None,
        analyzer_factory: AnalyzerFactory | None = None,
        finding_normalizer: FindingNormalizer | None = None,
    ):
        self._workspace_manager = workspace_manager or WorkspaceManager()
        self._analyzer_factory = analyzer_factory or AnalyzerFactory()
        self._finding_normalizer = finding_normalizer or FindingNormalizer()

    async def run(self, job: AnalysisJob) -> AnalysisResult:
        started_at = datetime.now(timezone.utc)

        async with self._workspace_manager.prepare(job) as workspace:
            languages = detect_languages(workspace.path)
            logger.info("[job:%s] detected languages: %s", job.job_id, sorted(languages))

            analyzers = self._analyzer_factory.create_for_languages(languages)
            logger.info(
                "[job:%s] selected analyzers: %s", job.job_id, [a.tool_name for a in analyzers]
            )

            # Analyzers are independent — each only reads the checked-out
            # workspace, none write to it — so there's no reason to run
            # them one at a time.
            results = await asyncio.gather(
                *(analyzer.analyze(workspace, job) for analyzer in analyzers),
                return_exceptions=True,
            )

            # Must happen before the workspace context exits — the temp
            # checkout is deleted as soon as it does, and AnalysisMetrics
            # needs each file's line count independent of ESLint's output.
            file_lines = scan_js_ts_files(workspace.path)

        raw_findings, failed_tools = self._collect_results(analyzers, results, job)

        # If every selected analyzer failed, the job itself failed — a
        # "completed" result with zero findings would misrepresent an
        # outage as "nothing to report". If only *some* failed, the job
        # still completes with whatever findings the others genuinely
        # produced — one tool's failure shouldn't discard real results
        # from the rest.
        all_failed = bool(analyzers) and len(failed_tools) == len(analyzers)

        findings = self._finding_normalizer.normalize(raw_findings)

        return AnalysisResult(
            job_id=job.job_id,
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            branch=job.branch,
            status="failed" if all_failed else "completed",
            findings=findings,
            metrics=calculate_metrics(findings, file_lines),
            rule_statistics=calculate_rule_statistics(findings),
            file_statistics=calculate_file_statistics(findings, file_lines),
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            error_message="; ".join(failed_tools) if all_failed else None,
        )

    def _collect_results(
        self,
        analyzers: list[Analyzer],
        results: list[list[Finding] | BaseException],
        job: AnalysisJob,
    ) -> tuple[list[Finding], list[str]]:
        raw_findings: list[Finding] = []
        failed_tools: list[str] = []

        for analyzer, result in zip(analyzers, results):
            if isinstance(result, BaseException):
                logger.error("[job:%s] %s failed: %s", job.job_id, analyzer.tool_name, result)
                failed_tools.append(f"{analyzer.tool_name}: {result}")
                continue
            raw_findings.extend(result)

        return raw_findings, failed_tools
