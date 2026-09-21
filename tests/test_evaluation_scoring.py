from datetime import datetime, timezone
from uuid import uuid4

import pytest

from analysis_engine.domain import AgentFinding, AgentReview, ReviewEvidence, ReviewStats
from analysis_engine.evaluation.case import Case, Expected
from analysis_engine.evaluation.cases import ALL_CASES
from analysis_engine.evaluation.scoring import locate, score_case, summarise

_CASE = Case(
    id="t", title="t", description="t",
    base={"a.py": "x = 1\n", "caller.py": "one\ntwo\nconvert(a, b)\n"},
    head={"a.py": "line1\nline2\nbug_here()\nline4\n"},
    expected=(Expected("a.py", "bug_here()", "the bug", also=(("caller.py", "convert(a, b)"),)),),
)
_CLEAN = Case(id="c", title="c", description="c", base={"a.py": "x\n"}, head={"a.py": "y\n"})


def _finding(path, line, title="issue"):
    return AgentFinding(fingerprint=title, title=title, category="correctness", severity="high", confidence=0.9,
                        file_path=path, line_start=line, line_end=line, explanation="e",
                        evidence=[ReviewEvidence(type="code_location", ref=f"{path}:{line}", verified=True)])


def _review(*findings, status="completed", cost=0.001):
    return AgentReview(result_id=uuid4(), job_id=uuid4(), repository="r", pull_request_number=1, status=status,
                       findings=list(findings), started_at=datetime.now(timezone.utc),
                       stats=ReviewStats(model="m", stop_reason="submitted", cost_usd=cost, duration_ms=1000))


def test_every_shipped_case_locates_its_defects():
    for case in ALL_CASES:
        for expected in case.expected:
            assert locate(case, expected).spans


def test_a_finding_near_the_defect_counts_as_found():
    score = score_case(_CASE, _review(_finding("a.py", 5)))   # defect on line 3, tolerance 3

    assert (score.found, score.true_positives, score.false_positives) == (1, 1, 0)


def test_a_finding_at_the_alternative_location_counts_too():
    assert score_case(_CASE, _review(_finding("caller.py", 3))).found == 1


def test_findings_elsewhere_are_false_alarms_and_the_defect_is_missed():
    score = score_case(_CASE, _review(_finding("a.py", 20), _finding("other.py", 3)))

    assert score.found == 0 and score.false_positives == 2 and score.missed == ["the bug"]


def test_two_findings_on_one_defect_count_once_for_recall():
    score = score_case(_CASE, _review(_finding("a.py", 3, "one"), _finding("a.py", 4, "two")))

    assert score.found == 1 and score.true_positives == 2


def test_a_failed_review_misses_everything():
    score = score_case(_CASE, _review(status="failed"))

    assert score.found == 0 and score.missed == ["the bug"] and score.status == "failed"


def test_summary_metrics():
    scores = [
        score_case(_CASE, _review(_finding("a.py", 3), _finding("x.py", 1))),   # 1 TP, 1 FP
        score_case(_CASE, _review()),                                            # missed
        score_case(_CLEAN, _review(_finding("a.py", 1))),                        # false alarm on a clean PR
        score_case(_CLEAN, _review()),
    ]
    summary = summarise(scores)

    assert summary.recall == pytest.approx(0.5)
    assert summary.precision == pytest.approx(1 / 3)
    assert summary.clean_false_alarm_rate == pytest.approx(0.5)
    assert summary.total_cost_usd == pytest.approx(0.004)
