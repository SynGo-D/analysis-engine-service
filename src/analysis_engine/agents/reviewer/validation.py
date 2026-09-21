import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from ...domain import Finding
from ...domain.agent_review import AgentFinding, DroppedCandidate, DroppedTriage, ReviewEvidence, TriagedLinterFinding
from ...retrieval import PathNotAllowed, resolve_finding_ref, resolve_in_workspace
from .schema import CandidateIssue, Evidence, ReviewerOutput

# A quote may sit a line or two off the cited line: models count lines
# imperfectly, and that's not the same failure as inventing code.
_LINE_TOLERANCE = 2
_LINE_NUMBER_PREFIX = re.compile(r"^\s*\d+\|\s?", re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")


@dataclass
class ValidatedReview:
    summary: str
    areas_touched: list[str]
    findings: list[AgentFinding]
    linter_triage: list[TriagedLinterFinding]
    dropped: list[DroppedCandidate] = field(default_factory=list)
    triage_dropped: list[DroppedTriage] = field(default_factory=list)


def validate_review(output: ReviewerOutput, workspace: Path, findings: list[Finding], repository: str) -> ValidatedReview:
    """
    Checks every claim the Reviewer made against the repository, and keeps
    only what holds up (docs/agent-architecture.md §8.1).

    This is the first line of defence against a model that misreads or
    invents code. A candidate is dropped when:
      - its file or lines don't exist;
      - any quote doesn't appear at the location it cites;
      - it cites a linter finding that doesn't exist;
      - none of its evidence could be verified at all.
    Dropped candidates are recorded with the reason (for evaluation), never
    shown as findings.
    """
    files = _FileCache(workspace)
    kept: list[AgentFinding] = []
    dropped: list[DroppedCandidate] = []

    for candidate in output.candidates:
        problem, evidence = _check_candidate(candidate, files, findings)
        if problem:
            dropped.append(DroppedCandidate(title=candidate.title, reason=problem))
            continue
        kept.append(AgentFinding(
            fingerprint=_fingerprint(repository, candidate),
            title=candidate.title,
            category=candidate.category,
            severity=candidate.severity,
            confidence=candidate.confidence,
            file_path=candidate.file_path,
            line_start=candidate.line_start,
            line_end=max(candidate.line_end, candidate.line_start),
            explanation=candidate.explanation,
            evidence=evidence,
            suggested_fix=candidate.suggested_fix,
        ))

    triage = []
    triage_dropped = []
    for item in output.linter_triage:
        finding = resolve_finding_ref(item.ref, findings)
        if finding is None:
            # Recorded, not silent: a pattern of these means the prompt or
            # the reference format needs fixing.
            triage_dropped.append(DroppedTriage(ref=item.ref, reason="matches no linter finding"))
            continue
        triage.append(TriagedLinterFinding(
            fingerprint=finding.fingerprint, tool=finding.tool, rule_id=finding.rule_id,
            file_path=finding.file_path, line=finding.line, message=finding.message,
            importance=item.importance, reason=item.reason,
        ))

    return ValidatedReview(
        summary=output.summary.strip(),
        areas_touched=[a.strip() for a in output.areas_touched if a.strip()],
        findings=kept,
        linter_triage=triage,
        dropped=dropped,
        triage_dropped=triage_dropped,
    )


# -----------------------------------------------------------------------


def _check_candidate(
    candidate: CandidateIssue, files: "_FileCache", findings: list[Finding]
) -> tuple[str | None, list[ReviewEvidence]]:
    lines = files.lines(candidate.file_path)
    if lines is None:
        return f"file does not exist: {candidate.file_path}", []
    if candidate.line_start > len(lines):
        return f"line {candidate.line_start} is past the end of {candidate.file_path} ({len(lines)} lines)", []

    checked: list[ReviewEvidence] = []
    for item in candidate.evidence:
        problem, verified = _check_evidence(item, files, findings)
        if problem:
            return problem, []
        checked.append(ReviewEvidence(type=item.type, ref=item.ref, quote=item.quote, verified=verified))

    if not any(e.verified for e in checked):
        return "no evidence could be verified (needs a matching code quote or a real linter finding)", []
    return None, checked


def _check_evidence(item: Evidence, files: "_FileCache", findings: list[Finding]) -> tuple[str | None, bool]:
    if item.type == "linter_finding":
        if resolve_finding_ref(item.ref, findings) is None:
            return f"cites a linter finding that doesn't exist: {item.ref}", False
        return None, True

    if item.type == "code_location":
        location = _parse_location(item.ref)
        if location is None:
            return f"unreadable location: {item.ref}", False
        path, start, end = location
        lines = files.lines(path)
        if lines is None or start > len(lines):
            return f"location does not exist: {item.ref}", False
        if not item.quote or not item.quote.strip():
            return None, False  # a location with nothing to check
        window = lines[max(start - 1 - _LINE_TOLERANCE, 0): min(end + _LINE_TOLERANCE, len(lines))]
        if _normalise(_LINE_NUMBER_PREFIX.sub("", item.quote)) not in _normalise("\n".join(window)):
            return f"quoted code is not at {item.ref}", False
        return None, True

    if item.type == "test":
        path = item.ref.split("::", 1)[0]
        if files.lines(path) is None:
            return f"test file does not exist: {path}", False
        return None, True

    return None, False  # call_path: accepted, but not mechanically checked


def _parse_location(ref: str) -> tuple[str, int, int] | None:
    path, _, span = ref.strip().rpartition(":")
    if not path:
        return None
    start_text, _, end_text = span.partition("-")
    if not start_text.isdigit() or (end_text and not end_text.isdigit()):
        return None
    start = int(start_text)
    end = int(end_text) if end_text else start
    return (path, start, max(end, start)) if start >= 1 else None


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _fingerprint(repository: str, candidate: CandidateIssue) -> str:
    # Excludes line numbers: they shift between pushes to the same PR, and
    # feedback on an issue should follow it (§10.1).
    title = _normalise(candidate.title).lower()
    raw = "|".join([repository, candidate.category, candidate.file_path, title])
    return hashlib.sha256(raw.encode()).hexdigest()


class _FileCache:
    """Reads each workspace file once, through the same path guard as the tools."""

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._cache: dict[str, list[str] | None] = {}

    def lines(self, path: str) -> list[str] | None:
        if path not in self._cache:
            try:
                resolved = resolve_in_workspace(self._workspace, path)
                self._cache[path] = (
                    resolved.read_text(encoding="utf-8", errors="replace").splitlines() if resolved.is_file() else None
                )
            except (OSError, PathNotAllowed):
                self._cache[path] = None
        return self._cache[path]
