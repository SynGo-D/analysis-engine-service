import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..agents.reviewer.validation import FileCache, check_evidence
from ..agents.runtime.agent_loop import AgentRun
from ..agents.runtime.provider import LLMProvider, Usage
from ..agents.verifier.agent import run_verifier
from ..agents.verifier.schema import VerifierOutput
from ..config import settings
from ..domain import AgentFinding, AnalysisJob, ChangeSet, DroppedCandidate, Finding
from ..domain.code_index import RepoIndex
from ..retrieval import RetrievalTools, ToolExecutor, numbered_lines, redact
from ..workspace import WorkspaceSecurityError, run_git_output

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
_CONTEXT_LINES = 25          # around the issue when it isn't inside a known function
_SYMBOL_MAX_LINES = 150
_FILE_DIFF_CHARS = 6_000


@dataclass
class VerificationResult:
    kept: list[AgentFinding]
    refuted: list[DroppedCandidate]
    usage: Usage = field(default_factory=Usage)
    cost_usd: float | None = 0.0
    calls: int = 0


async def verify_findings(
    provider: LLMProvider,
    findings: list[AgentFinding],
    *,
    workspace: Path,
    index: RepoIndex,
    linter_findings: list[Finding],
    job: AnalysisJob,
    change_set: ChangeSet,
    summary: str,
) -> VerificationResult:
    """
    Agent 2 (docs/agent-architecture.md §8.2): each issue is checked by a
    separate Verifier call with a fresh context, in parallel.

    The verdict is applied under rules enforced here, not by the model:
      - a drop that cites code which isn't there is rejected (the issue is
        kept), so a hallucinated reason can't delete a real finding;
      - only an explicit "drop" verdict removes an issue. When the checks
        contradict a "keep" verdict, the issue stays but is left
        unverified: dropping a real bug costs more than showing one that
        couldn't be confirmed (evaluation showed the model mislabelling
        checks);
      - severity can only go down;
      - a Verifier that times out or answers badly drops the issue — an
        issue nobody could confirm isn't shown;
      - but a provider outage keeps the issues, marked unverified:
        otherwise one outage would silently empty every review.
    """
    result = VerificationResult(kept=[], refuted=[])
    if not findings:
        return result

    semaphore = asyncio.Semaphore(settings.verifier_concurrency)
    files = FileCache(workspace)

    async def one(finding: AgentFinding) -> tuple[AgentFinding, AgentRun[VerifierOutput]]:
        async with semaphore:
            content = await _brief(finding, workspace, index, job, change_set, summary)
            executor = ToolExecutor(RetrievalTools(workspace, index, linter_findings))
            return finding, await run_verifier(provider, content, executor)

    for finding, run in await asyncio.gather(*(one(f) for f in findings)):
        result.calls += 1
        result.usage = result.usage + run.usage
        result.cost_usd = None if result.cost_usd is None or run.cost_usd is None else result.cost_usd + run.cost_usd
        _apply(finding, run, result, files, linter_findings)

    logger.info("[job:%s] verifier: %d kept, %d refuted, $%s", job.job_id, len(result.kept), len(result.refuted),
                f"{result.cost_usd:.5f}" if result.cost_usd is not None else "unknown")
    return result


def _apply(finding: AgentFinding, run: AgentRun[VerifierOutput], result: VerificationResult,
           files: FileCache, linter_findings: list[Finding]) -> None:
    verdict = run.output

    if verdict is None:
        if run.stop_reason == "provider_error":
            result.kept.append(finding)  # stays "unverified"
        else:
            result.refuted.append(DroppedCandidate(
                title=finding.title, stage="verifier",
                reason=f"could not be confirmed (verifier {run.stop_reason.replace('_', ' ')})"))
        return

    if verdict.verdict == "drop":
        invented = [e for e in verdict.counter_evidence if check_evidence(e, files, linter_findings)[0]]
        if invented:
            logger.warning("verifier cited code that isn't there; keeping issue %r", finding.title)
            result.kept.append(finding)  # stays "unverified": the refutation was unsound
            return
        result.refuted.append(DroppedCandidate(title=finding.title, reason=verdict.reason, stage="verifier"))
        return

    if any(check.refutes_issue for check in verdict.checks):
        result.kept.append(finding)  # inconsistent answer: shown, but not marked verified
        return

    finding.verification = "verified"
    if verdict.adjusted_severity and _SEVERITY_RANK[verdict.adjusted_severity] < _SEVERITY_RANK[finding.severity]:
        finding.severity = verdict.adjusted_severity
    result.kept.append(finding)


async def _brief(finding: AgentFinding, workspace: Path, index: RepoIndex, job: AnalysisJob,
                 change_set: ChangeSet, summary: str) -> str:
    """Everything one Verifier call starts from: intent, the claim, the code, that file's diff."""
    evidence = "\n".join(
        f"- {e.type} {e.ref}" + (f": {e.quote}" if e.quote else "") for e in finding.evidence
    )
    parts = [
        "## Pull request",
        f"Title: {job.title or '(no title)'}",
        f"Description: {(job.description or '(none)').strip()[:2000]}",
        f"Summary of the change: {summary}",
        "",
        "## Reported issue",
        f"{finding.severity} {finding.category}: {finding.title}",
        f"Location: {finding.file_path}:{finding.line_start}-{finding.line_end}",
        f"Claim: {finding.explanation}",
        "Evidence:",
        evidence,
        "",
        "## Code around the issue",
        _code_around(finding, workspace, index),
        "",
        f"## Diff of {finding.file_path} in this PR",
        await _file_diff(workspace, change_set, finding.file_path),
    ]
    return redact("\n".join(parts))


def _code_around(finding: AgentFinding, workspace: Path, index: RepoIndex) -> str:
    lines = FileCache(workspace).lines(finding.file_path)
    if not lines:
        return "(file not readable)"

    containing = [s for s in index.symbols_in_file(finding.file_path) if s.contains_line(finding.line_start)]
    if containing:
        symbol = min(containing, key=lambda s: s.end_line - s.start_line)
        start, end = symbol.start_line, min(symbol.end_line, symbol.start_line + _SYMBOL_MAX_LINES - 1)
        header = f"{symbol.kind} {symbol.qualified_name} [id: {symbol.symbol_id}]"
    else:
        start = max(finding.line_start - _CONTEXT_LINES, 1)
        end = min(finding.line_end + _CONTEXT_LINES, len(lines))
        header = f"{finding.file_path} lines {start}-{end}"
    return f"{header}\n{numbered_lines(lines, start, min(end, len(lines)))}"


async def _file_diff(workspace: Path, change_set: ChangeSet, path: str) -> str:
    if change_set.file(path) is None:
        return "(this file is not changed by the PR)"
    try:
        _code, output = await run_git_output(
            ["-c", "core.quotePath=false", "diff", "--unified=3", "--no-color", "--no-ext-diff", "--no-textconv",
             "-M", change_set.base_sha, change_set.head_sha, "--", f":(literal){path}"],
            workspace, settings.git_clone_timeout_seconds,
        )
    except WorkspaceSecurityError:
        return "(diff unavailable)"
    text = output.decode("utf-8", errors="replace")
    return text[:_FILE_DIFF_CHARS] + ("\n[diff truncated]" if len(text) > _FILE_DIFF_CHARS else "")
