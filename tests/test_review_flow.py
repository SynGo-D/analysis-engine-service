import asyncio
from contextlib import asynccontextmanager

import pytest

from analysis_engine.application.orchestrator import AnalysisOrchestrator
from analysis_engine.config import settings
from analysis_engine.review import ReviewOrchestrator
from analysis_engine.workspace import Workspace

from .fake_provider import FakeProvider, ProviderError, call, turn
from .test_context_pack import _DISCOUNT_FIX, _JOB, _setup


class _Workspaces:
    def __init__(self, path):
        self.path = path

    @asynccontextmanager
    async def prepare(self, job):
        yield Workspace(job_id=job.job_id, path=self.path)


class _NoAnalyzers:
    def create_for_languages(self, languages):
        return []


_REVIEW = {
    "summary": "Applies the discount before tax.",
    "areas_touched": ["billing"],
    "linter_triage": [],
    "candidates": [
        {"title": "Negative totals possible", "category": "correctness", "severity": "medium", "confidence": 0.8,
         "file_path": "billing/total.py", "line_start": 2, "line_end": 2,
         "explanation": "A discount larger than the price gives a negative total.",
         "evidence": [{"type": "code_location", "ref": "billing/total.py:2", "quote": "return (price - discount) * 1.2"}],
         "suggested_fix": None},
        {"title": "Invented problem", "category": "security", "severity": "high", "confidence": 0.9,
         "file_path": "billing/total.py", "line_start": 1, "line_end": 1, "explanation": "Made up.",
         "evidence": [{"type": "code_location", "ref": "billing/total.py:1", "quote": "eval(user_input)"}],
         "suggested_fix": None},
    ],
}


def _verdict(verdict="keep", checks=(), severity=None, counter=()):
    return {"verdict": verdict, "reason": "checked", "checks": list(checks), "adjusted_severity": severity,
            "counter_evidence": list(counter)}


KEEP = _verdict()


def _run(tmp_path, provider):
    events = []

    async def on_result(result):
        events.append(("result", result.status))

    async def on_review(review):
        events.append(("review", review.status))

    orchestrator = AnalysisOrchestrator(
        workspace_manager=_Workspaces(_setup(tmp_path, _DISCOUNT_FIX)),
        analyzer_factory=_NoAnalyzers(),
        review_orchestrator=ReviewOrchestrator(provider),
    )
    result = asyncio.run(orchestrator.run(_JOB, on_result=on_result, on_review=on_review))
    return result, events


def test_reviews_the_pr_and_keeps_only_claims_that_check_out(tmp_path):
    provider = FakeProvider([turn(call("submit_review", _REVIEW)), turn(call("submit_verdict", KEEP))])

    result, events = _run(tmp_path, provider)
    review = result.review

    assert review.status == "completed"
    assert review.summary == "Applies the discount before tax."
    assert [f.title for f in review.findings] == ["Negative totals possible"]
    assert [d.title for d in review.dropped] == ["Invented problem"]   # its quote isn't in the code
    assert review.risk_level == "medium"
    assert review.stats.candidates_proposed == 2 and review.stats.findings_reported == 1
    assert review.stats.cost_usd > 0
    # Only the issue that passed the evidence checks was sent to the Verifier.
    assert review.stats.verifier_calls == 1
    assert review.findings[0].verification == "verified"
    verifier_brief = provider.requests[1].items[0]["content"]
    assert "Negative totals possible" in verifier_brief and "Invented problem" not in verifier_brief
    # The Reviewer was given the real diff.
    assert "(price - discount) * 1.2" in provider.requests[0].items[0]["content"]


def test_the_linter_result_is_saved_before_the_review_starts(tmp_path):
    _, events = _run(tmp_path, FakeProvider([turn(call("submit_review", _REVIEW)), turn(call("submit_verdict", KEEP))]))

    assert events == [("result", "completed"), ("review", "running"), ("review", "completed")]


def test_a_failed_review_leaves_the_linter_result_intact(tmp_path):
    result, events = _run(tmp_path, FakeProvider([ProviderError("OpenAI unavailable")]))

    assert result.status == "completed"
    assert result.review.status == "failed" and "OpenAI unavailable" in result.review.error_message
    assert events[0] == ("result", "completed") and events[-1] == ("review", "failed")


def test_without_a_provider_the_review_is_skipped_as_disabled(tmp_path):
    result, events = _run(tmp_path, None)

    assert result.review.status == "skipped" and result.review.skip_reason == "disabled"
    # No "running" row for a review that never started.
    assert events == [("result", "completed"), ("review", "skipped")]


def test_a_pr_over_the_size_limit_is_skipped_without_calling_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "review_max_changed_lines", 1)
    provider = FakeProvider([])

    result, _ = _run(tmp_path, provider)

    assert result.review.skip_reason == "too_large"
    assert provider.requests == []


# ---------------------------------------------------------------------------
# Verifier verdicts, applied under the engine's rules
# ---------------------------------------------------------------------------


def _verified_review(tmp_path, *verifier_turns):
    result, _ = _run(tmp_path, FakeProvider([turn(call("submit_review", _REVIEW)), *verifier_turns]))
    return result.review


def test_a_refuted_issue_is_dropped_and_recorded(tmp_path):
    review = _verified_review(tmp_path, turn(call("submit_verdict", _verdict("drop"))))

    assert review.findings == []
    assert [(d.title, d.stage) for d in review.dropped if d.stage == "verifier"] == [("Negative totals possible", "verifier")]
    assert review.stats.candidates_refuted == 1 and review.risk_level == "low"


def test_a_drop_citing_code_that_isnt_there_is_rejected(tmp_path):
    invented = {"type": "code_location", "ref": "billing/total.py:2", "quote": "if discount > price: raise"}
    review = _verified_review(tmp_path, turn(call("submit_verdict", _verdict("drop", counter=[invented]))))

    assert [f.title for f in review.findings] == ["Negative totals possible"]
    assert review.findings[0].verification == "unverified"


def test_checks_that_contradict_a_keep_verdict_leave_the_issue_unverified_not_dropped(tmp_path):
    # Found in evaluation: the model marked "was this intended? — no" as
    # refuting, which silently discarded real bugs. Only a drop verdict drops.
    checks = [{"question": "intended", "refutes_issue": True, "note": "description promises unchanged rights"}]
    review = _verified_review(tmp_path, turn(call("submit_verdict", _verdict("keep", checks=checks))))

    assert [f.verification for f in review.findings] == ["unverified"]


def test_severity_can_be_lowered_but_never_raised(tmp_path):
    lowered = _verified_review(tmp_path, turn(call("submit_verdict", _verdict(severity="low"))))
    assert lowered.findings[0].severity == "low"


def test_severity_is_never_raised(tmp_path):
    kept = _verified_review(tmp_path, turn(call("submit_verdict", _verdict(severity="high"))))
    assert kept.findings[0].severity == "medium"


def test_an_unconfirmed_issue_is_dropped(tmp_path):
    bad = {"verdict": "maybe"}
    review = _verified_review(tmp_path, turn(call("submit_verdict", bad)), turn(call("submit_verdict", bad)))

    assert review.findings == [] and "could not be confirmed" in review.dropped[-1].reason


def test_a_provider_outage_keeps_issues_unverified_rather_than_hiding_them(tmp_path):
    review = _verified_review(tmp_path, ProviderError("OpenAI unavailable"))

    assert [f.verification for f in review.findings] == ["unverified"]


def test_verifier_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "verifier_enabled", False)
    review = _verified_review(tmp_path)

    assert review.stats.verifier_calls == 0 and review.findings[0].verification == "unverified"
