import hashlib

from analysis_engine.agents.reviewer import ReviewerOutput, validate_review
from analysis_engine.domain import AgentFinding, Finding, ReviewEvidence
from analysis_engine.review import MAX_REPORTED, rank_and_cap, risk_level

_CODE = "def total(price, discount):\n    taxed = price * 1.2\n    return taxed - discount\n"


def _finding(line=2) -> Finding:
    return Finding(repository="a/b", pull_request_number=1, commit_sha="h", file_path="billing.py", line=line,
                   severity="warning", category="code_smell", rule_id="r", message="m", tool="pylint",
                   fingerprint=hashlib.sha256(str(line).encode()).hexdigest())


def _candidate(evidence, **overrides):
    return {"title": "Discount applied after tax", "category": "correctness", "severity": "high", "confidence": 0.9,
            "file_path": "billing.py", "line_start": 2, "line_end": 3, "explanation": "Tax is charged on the discount.",
            "evidence": evidence, "suggested_fix": None, **overrides}


def _validate(tmp_path, candidates, triage=(), findings=None):
    (tmp_path / "billing.py").write_text(_CODE)
    output = ReviewerOutput.model_validate({"summary": " Adds discounts. ", "areas_touched": ["billing"],
                                            "linter_triage": list(triage), "candidates": candidates})
    return validate_review(output, tmp_path, findings or [_finding()], "a/b")


def _loc(ref, quote):
    return {"type": "code_location", "ref": ref, "quote": quote}


def test_keeps_a_candidate_whose_quote_is_really_there(tmp_path):
    result = _validate(tmp_path, [_candidate([_loc("billing.py:3", "return taxed - discount")])])

    assert len(result.findings) == 1 and result.findings[0].evidence[0].verified
    assert result.summary == "Adds discounts."


def test_tolerates_line_number_prefixes_whitespace_and_small_line_drift(tmp_path):
    quote = "    3|  return   taxed - discount"
    result = _validate(tmp_path, [_candidate([_loc("billing.py:1", quote)])])   # cited 2 lines off

    assert len(result.findings) == 1


def test_drops_a_candidate_quoting_code_that_is_not_there(tmp_path):
    result = _validate(tmp_path, [_candidate([_loc("billing.py:3", "return price - discount * 1.2")])])

    assert result.findings == []
    assert "quoted code is not at billing.py:3" in result.dropped[0].reason


def test_drops_candidates_pointing_at_missing_files_or_lines(tmp_path):
    missing_file = _candidate([_loc("nope.py:1", "x")], file_path="nope.py")
    past_the_end = _candidate([_loc("billing.py:3", "return taxed - discount")], line_start=99, line_end=99)

    result = _validate(tmp_path, [missing_file, past_the_end])

    assert result.findings == [] and len(result.dropped) == 2


def test_refuses_evidence_that_tries_to_read_outside_the_repository(tmp_path):
    result = _validate(tmp_path, [_candidate([_loc("../../etc/passwd:1", "root")])])

    assert result.findings == []


def test_needs_at_least_one_verified_piece_of_evidence(tmp_path):
    unverifiable = _candidate([{"type": "call_path", "ref": "checkout → total", "quote": None}])

    result = _validate(tmp_path, [unverifiable])

    assert result.findings == [] and "no evidence could be verified" in result.dropped[0].reason


def test_a_real_linter_finding_counts_as_evidence_and_an_invented_one_drops_it(tmp_path):
    real = _finding()
    good = _candidate([{"type": "linter_finding", "ref": real.fingerprint[:10], "quote": None}])
    bad = _candidate([{"type": "linter_finding", "ref": "deadbeef00", "quote": None}], title="Other")

    result = _validate(tmp_path, [good, bad], findings=[real])

    assert [f.title for f in result.findings] == ["Discount applied after tax"]


def test_linter_triage_keeps_only_real_refs_and_expands_them(tmp_path):
    real = _finding()
    triage = [{"ref": real.fingerprint[:10], "importance": "high", "reason": "Unused discount branch."},
              {"ref": "0123456789", "importance": "low", "reason": "invented"}]

    result = _validate(tmp_path, [], triage=triage, findings=[real])

    assert [(t.rule_id, t.line, t.importance) for t in result.linter_triage] == [("r", 2, "high")]


def test_fingerprint_ignores_line_numbers_so_feedback_survives_new_pushes(tmp_path):
    ev = [_loc("billing.py:3", "return taxed - discount")]
    first = _validate(tmp_path, [_candidate(ev)]).findings[0]
    shifted = _validate(tmp_path, [_candidate(ev, line_start=3, line_end=3)]).findings[0]

    assert first.fingerprint == shifted.fingerprint


# ---------------------------------------------------------------------------
# Ranking and capping
# ---------------------------------------------------------------------------


def _agent_finding(title, severity, confidence, line=1, category="correctness"):
    return AgentFinding(fingerprint=title, title=title, category=category, severity=severity, confidence=confidence,
                        file_path="a.py", line_start=line, line_end=line, explanation="e",
                        evidence=[ReviewEvidence(type="code_location", ref=f"a.py:{line}", verified=True)])


def test_ranks_by_severity_times_confidence_and_caps():
    findings = [_agent_finding(f"f{i}", "low", 0.9, line=i * 10) for i in range(10)]
    findings.append(_agent_finding("top", "high", 0.6, line=500))

    ranked = rank_and_cap(findings)

    assert ranked[0].title == "top" and len(ranked) == MAX_REPORTED


def test_merges_overlapping_issues_of_the_same_kind():
    a = _agent_finding("strong", "high", 0.9, line=5)
    b = _agent_finding("weak", "medium", 0.6, line=5)
    b.evidence = [ReviewEvidence(type="test", ref="tests/test_a.py", verified=True)]

    ranked = rank_and_cap([b, a])

    assert [f.title for f in ranked] == ["strong"]
    assert {e.ref for e in ranked[0].evidence} == {"a.py:5", "tests/test_a.py"}


def test_risk_level_follows_the_worst_reported_issue():
    assert risk_level([]) == "low"
    assert risk_level([_agent_finding("m", "medium", 0.9)]) == "medium"
    assert risk_level([_agent_finding("m", "medium", 0.9), _agent_finding("h", "high", 0.5, line=9)]) == "high"


def test_accepts_refs_copied_with_their_display_brackets(tmp_path):
    # Found in the first real review: the model copied "[ref]" verbatim,
    # and every triage entry was silently rejected.
    real = _finding()
    triage = [{"ref": f"[{real.fingerprint[:10]}]", "importance": "high", "reason": "Breaks module loading."}]

    result = _validate(tmp_path, [], triage=triage, findings=[real])

    assert len(result.linter_triage) == 1


def test_records_triage_entries_it_had_to_drop(tmp_path):
    result = _validate(tmp_path, [], triage=[{"ref": "0123456789", "importance": "low", "reason": "x"}])

    assert [d.ref for d in result.triage_dropped] == ["0123456789"]
