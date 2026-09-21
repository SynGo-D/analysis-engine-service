import math
from dataclasses import dataclass, field

from ..domain import AgentFinding, AgentReview
from .case import Case, Expected

# A reported issue counts as finding a planted defect if it's in the same
# file and its line range comes within this many lines of the defect.
# Models cite the line where a problem shows, not always the line that
# causes it.
LINE_TOLERANCE = 3


@dataclass
class ExpectedLocation:
    expected: Expected
    # Every (file, first line, last line) where reporting this defect counts.
    spans: list[tuple[str, int, int]]


@dataclass
class CaseScore:
    case_id: str
    clean: bool
    status: str
    expected: int = 0
    found: int = 0                # planted defects matched by a reported issue
    reported: int = 0
    true_positives: int = 0       # reported issues that match a planted defect
    false_positives: int = 0
    dropped_by_validation: int = 0
    missed: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


def locate(case: Case, expected: Expected) -> ExpectedLocation:
    """Where an expected defect is, found by its snippet(s) in the head version of the repository."""
    spans = [_span(case, file, snippet) for file, snippet in ((expected.file, expected.snippet), *expected.also)]
    return ExpectedLocation(expected, spans)


def _span(case: Case, file: str, snippet: str) -> tuple[str, int, int]:
    content = case.head[file] if file in case.head else case.base.get(file)
    if content is None:
        raise ValueError(f"{case.id}: expected file {file} is not in the head version")
    index = content.find(snippet)
    if index < 0:
        raise ValueError(f"{case.id}: snippet not found in {file}: {snippet!r}")
    start = content.count("\n", 0, index) + 1
    return file, start, start + snippet.count("\n")


def score_case(case: Case, review: AgentReview | None) -> CaseScore:
    locations = [locate(case, e) for e in case.expected]
    score = CaseScore(case_id=case.id, clean=case.is_clean, status=review.status if review else "none",
                      expected=len(locations))

    if review is None or review.status != "completed":
        score.missed = [loc.expected.what for loc in locations]
        score.error = (review.error_message or review.skip_reason) if review else "no review"
        _add_stats(score, review)
        return score

    matched: set[int] = set()
    for finding in review.findings:
        hits = [i for i, loc in enumerate(locations) if _matches(finding, loc)]
        if hits:
            score.true_positives += 1
            matched.update(hits)
        else:
            score.false_positives += 1
            score.unexpected.append(f"{finding.title} ({finding.file_path}:{finding.line_start})")

    score.reported = len(review.findings)
    score.found = len(matched)
    score.missed = [loc.expected.what for i, loc in enumerate(locations) if i not in matched]
    score.dropped_by_validation = len(review.dropped)
    _add_stats(score, review)
    return score


@dataclass
class Summary:
    cases: int
    buggy_cases: int
    clean_cases: int
    precision: float | None
    recall: float | None
    clean_false_alarm_rate: float | None   # false alarms per clean PR
    failed_reviews: int
    total_cost_usd: float
    cost_per_pr_usd: float
    p95_duration_s: float
    dropped_by_validation: int


def summarise(scores: list[CaseScore]) -> Summary:
    buggy = [s for s in scores if not s.clean]
    clean = [s for s in scores if s.clean]
    reported = sum(s.reported for s in scores)
    expected = sum(s.expected for s in buggy)
    durations = sorted(s.duration_ms for s in scores)
    p95 = durations[max(math.ceil(0.95 * len(durations)) - 1, 0)] / 1000 if durations else 0.0
    total_cost = sum(s.cost_usd for s in scores)

    return Summary(
        cases=len(scores),
        buggy_cases=len(buggy),
        clean_cases=len(clean),
        precision=sum(s.true_positives for s in scores) / reported if reported else None,
        recall=sum(s.found for s in buggy) / expected if expected else None,
        clean_false_alarm_rate=sum(s.false_positives for s in clean) / len(clean) if clean else None,
        failed_reviews=sum(1 for s in scores if s.status != "completed"),
        total_cost_usd=total_cost,
        cost_per_pr_usd=total_cost / len(scores) if scores else 0.0,
        p95_duration_s=p95,
        dropped_by_validation=sum(s.dropped_by_validation for s in scores),
    )


def _matches(finding: AgentFinding, location: ExpectedLocation) -> bool:
    return any(
        finding.file_path == file
        and finding.line_start <= end + LINE_TOLERANCE
        and start <= finding.line_end + LINE_TOLERANCE
        for file, start, end in location.spans
    )


def _add_stats(score: CaseScore, review: AgentReview | None) -> None:
    stats = review.stats if review else None
    if stats:
        score.cost_usd = stats.cost_usd or 0.0
        score.duration_ms = stats.duration_ms
        score.input_tokens = stats.input_tokens
        score.cached_tokens = stats.cached_tokens
        score.output_tokens = stats.output_tokens
