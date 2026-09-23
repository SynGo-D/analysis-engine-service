import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .finding_normalizer import FindingNormalizer
from ..analyzers import Analyzer
from ..diffing import DiffExtractor, changed_symbols, mark_findings
from ..domain import AgentReview, AnalysisJob, AnalysisResult, Finding, PullRequestChanges, PythonAnalysisResult
from ..domain.code_index import RepoIndex
from ..factories import AnalyzerFactory, detect_languages
from ..indexing import CodeIndexer
from ..metrics import calculate_file_statistics, calculate_metrics, calculate_rule_statistics, scan_source_files
from ..review import ReviewOrchestrator
from ..workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class AnalysisOrchestrator:
    """
    Coordinates the full analysis pipeline for one job:

        validate job -> obtain repository -> create workspace
          -> checkout commit -> detect languages -> select analyzers
          -> run ESLint/Python analyzers  (concurrently with: work out
             what the PR changed, then which symbols that touches)
          -> normalize findings -> mark findings on changed lines
          -> calculate metrics
          -> hand the result to the caller to save   (Stage 1 ends)
          -> AI review, while the checkout still exists (Stage 2)
          -> return the result, with the review attached

    This is the only place that sequence is encoded (Pipeline/Command
    Pattern) — the consumer just calls `run()` and turns the outcome into
    an ack/nack. A polyglot repository runs every applicable analyzer
    concurrently (ESLint for JS/TS, PythonAnalyzer for Python) exactly
    the same way multiple JS/TS analyzers would have — this loop doesn't
    special-case language.
    """

    def __init__(
        self,
        workspace_manager: WorkspaceManager | None = None,
        analyzer_factory: AnalyzerFactory | None = None,
        finding_normalizer: FindingNormalizer | None = None,
        diff_extractor: DiffExtractor | None = None,
        code_indexer: CodeIndexer | None = None,
        review_orchestrator: ReviewOrchestrator | None = None,
    ):
        self._workspace_manager = workspace_manager or WorkspaceManager()
        self._analyzer_factory = analyzer_factory or AnalyzerFactory()
        self._finding_normalizer = finding_normalizer or FindingNormalizer()
        self._diff_extractor = diff_extractor or DiffExtractor()
        self._code_indexer = code_indexer or CodeIndexer()
        # None = no AI review stage at all (tests, or a deployment without it).
        self._review_orchestrator = review_orchestrator

    async def run(
        self,
        job: AnalysisJob,
        on_result: Callable[[AnalysisResult], Awaitable[None]] | None = None,
        on_review: Callable[[AgentReview], Awaitable[None]] | None = None,
    ) -> AnalysisResult:
        """
        `on_result` is called with the linter result as soon as it exists,
        *before* the AI review starts, so it can be saved and shown while
        the review runs (principle 5: the review can fail or take a
        minute; the linter result must not wait for it). `on_review` is
        called when the review starts and again when it ends.
        """
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
            # them one at a time. Working out the PR's changes runs
            # alongside them: it only fetches into .git, which no analyzer
            # reads, and its network round trip would otherwise add
            # straight to the job's duration.
            results, (changes, index) = await asyncio.gather(
                asyncio.gather(
                    *(analyzer.analyze(workspace, job) for analyzer in analyzers),
                    return_exceptions=True,
                ),
                self._extract_changes(workspace.path, job, workspace.git_env),
            )

            # Must happen before the workspace context exits — the temp
            # checkout is deleted as soon as it does, and AnalysisMetrics
            # needs each file's line count independent of any analyzer's
            # output. Scoped to the detected languages so the density
            # figures are measured against the code that was analyzed.
            file_lines = scan_source_files(workspace.path, languages)

            result = self._build_result(job, analyzers, results, changes, file_lines, started_at)
            if on_result is not None:
                await on_result(result)

            # Stage 2 runs inside the workspace block on purpose: the
            # Reviewer's tools read the checkout, which is deleted as soon
            # as this block exits.
            if self._review_orchestrator is not None and result.status == "completed":
                result.review = await self._run_review(workspace.path, job, result, index, on_review)

        return result

    def _build_result(self, job, analyzers, results, changes, file_lines, started_at) -> AnalysisResult:
        raw_findings, failed_tools = self._collect_results(analyzers, results, job)

        # If every selected analyzer failed, the job itself failed — a
        # "completed" result with zero findings would misrepresent an
        # outage as "nothing to report". If only *some* failed, the job
        # still completes with whatever findings the others genuinely
        # produced — one tool's failure shouldn't discard real results
        # from the rest.
        all_failed = bool(analyzers) and len(failed_tools) == len(analyzers)

        findings = self._finding_normalizer.normalize(raw_findings)

        if changes.status == "available" and changes.change_set is not None:
            findings, changes.findings_on_changed_lines = mark_findings(findings, changes.change_set)

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
            python=self._extract_python_result(analyzers),
            changes=changes,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            error_message="; ".join(failed_tools) if all_failed else None,
        )

    async def _run_review(self, workspace_path, job, result, index, on_review) -> AgentReview:
        review = self._review_orchestrator.pending(job, result)
        if on_review is not None and self._review_orchestrator.skip_reason(result.changes) is None:
            await on_review(review)  # lets a client show "AI review in progress"

        review = await self._review_orchestrator.run(workspace_path, job, result, index, review)
        if on_review is not None:
            await on_review(review)
        return review

    async def _extract_changes(
        self, workspace_path, job: AnalysisJob, git_env: dict[str, str] | None = None
    ) -> tuple[PullRequestChanges, RepoIndex | None]:
        """
        What the PR changed, plus the symbols those changes touch.

        Never lets an error escape: the PR view is an addition to the
        analysis, and a bug here must not turn a good linter run into a
        failed job.
        """
        try:
            changes = await self._diff_extractor.extract(workspace_path, job, git_env)
        except Exception:
            logger.exception("[job:%s] computing PR changes failed", job.job_id)
            return PullRequestChanges(status="unavailable", unavailable_reason="error"), None

        logger.info(
            "[job:%s] PR changes: %s%s", job.job_id, changes.status,
            f" ({changes.unavailable_reason})" if changes.unavailable_reason else
            f" ({changes.files_changed} files, +{changes.lines_added}/-{changes.lines_removed})",
        )

        index: RepoIndex | None = None
        if changes.status == "available" and changes.change_set is not None and changes.change_set.files:
            try:
                # tree-sitter parsing is CPU-bound and synchronous; off the
                # event loop so it doesn't stall the analyzers' subprocess I/O.
                index = await asyncio.to_thread(self._code_indexer.build, workspace_path)
                changes.changed_symbols = changed_symbols(index, changes.change_set)
            except Exception:
                logger.exception("[job:%s] indexing for changed symbols failed", job.job_id)

        return changes, index

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

    def _extract_python_result(self, analyzers: list[Analyzer]) -> PythonAnalysisResult | None:
        """
        Duck-typed rather than an isinstance check against a concrete
        PythonAnalyzer import — any future analyzer wanting to attach its
        own richer detail to AnalysisResult can expose the same
        `last_result` attribute without this orchestrator needing to know
        its concrete type. `None` if no analyzer exposed one (e.g. a
        pure-JS/TS job never ran PythonAnalyzer at all).
        """
        for analyzer in analyzers:
            result = getattr(analyzer, "last_result", None)
            if isinstance(result, PythonAnalysisResult):
                return result
        return None
