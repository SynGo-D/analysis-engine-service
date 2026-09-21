import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis_engine.agents.reviewer.schema import CandidateIssue
from analysis_engine.agents.reviewer.validation import _fingerprint
from analysis_engine.api import analysis as analysis_api
from analysis_engine.domain import AgentFinding, AgentReview, AnalysisResult, FeedbackSummary, ReviewEvidence

from .fake_provider import FakeProvider, call, turn
from .test_review_flow import KEEP, _REVIEW, _run


class _MemoryFeedback:
    def __init__(self):
        self.rows: dict[tuple[str, str, str], str] = {}

    async def record(self, repository, fingerprint, user_id, verdict, note, pull_request_number):
        self.rows[(repository, fingerprint, user_id)] = verdict

    async def summaries(self, repository, fingerprints, user_id=None):
        result = {}
        for (repo, fp, uid), verdict in self.rows.items():
            if repo == repository and fp in fingerprints:
                summary = result.setdefault(fp, FeedbackSummary())
                setattr(summary, verdict, getattr(summary, verdict) + 1)
                if uid == user_id:
                    summary.mine = verdict
        return result

    async def wrong_fingerprints(self, repository):
        return {fp for (repo, fp, _), v in self.rows.items() if repo == repository and v == "wrong"}


FP = "a" * 64


def _result():
    finding = AgentFinding(fingerprint=FP, title="t", category="correctness", severity="high", confidence=0.9,
                           file_path="a.py", line_start=1, line_end=1, explanation="e",
                           evidence=[ReviewEvidence(type="code_location", ref="a.py:1", verified=True)])
    now = datetime.now(timezone.utc)
    review = AgentReview(result_id=uuid4(), job_id=uuid4(), repository="acme/shop", pull_request_number=7,
                         status="completed", findings=[finding], started_at=now)
    return AnalysisResult(job_id=uuid4(), repository="acme/shop", pull_request_number=7, commit_sha="h",
                          branch="b", status="completed", started_at=now, review=review)


class _Results:
    async def get_latest_for_pull_request(self, repository, number):
        return _result()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(analysis_api.router)
    app.state.analysis_result_repository = _Results()
    app.state.feedback_repository = _MemoryFeedback()
    return TestClient(app)


URL = f"/api/repositories/acme/shop/analysis/pull-requests/7/review/findings/{FP}/feedback"


def test_feedback_is_recorded_per_user_and_shown_with_the_review(client):
    assert client.put(URL, json={"verdict": "useful"}, headers={"X-User-Id": "u1"}).status_code == 204
    assert client.put(URL, json={"verdict": "wrong"}, headers={"X-User-Id": "u2"}).status_code == 204
    # Changing your mind replaces your verdict rather than adding one.
    client.put(URL, json={"verdict": "not_useful"}, headers={"X-User-Id": "u1"})

    body = client.get("/api/repositories/acme/shop/analysis/pull-requests/7", headers={"X-User-Id": "u1"}).json()

    assert body["review"]["findings"][0]["feedback"] == {"useful": 0, "not_useful": 1, "wrong": 1, "mine": "not_useful"}


def test_feedback_needs_a_user(client):
    assert client.put(URL, json={"verdict": "useful"}).status_code == 401


@pytest.mark.parametrize("url, body", [
    (URL.replace(FP, "not-a-fingerprint"), {"verdict": "useful"}),
    (URL, {"verdict": "love it"}),
    (URL, {"verdict": "useful", "note": "x" * 1001}),
])
def test_invalid_feedback_is_rejected(client, url, body):
    assert client.put(url, json=body, headers={"X-User-Id": "u1"}).status_code == 422


# ---------------------------------------------------------------------------
# An issue marked wrong is not reported again
# ---------------------------------------------------------------------------


def test_an_issue_a_developer_marked_wrong_is_not_reported_again(tmp_path, monkeypatch):
    from analysis_engine.review import review_orchestrator as ro

    candidate = CandidateIssue.model_validate(_REVIEW["candidates"][0])
    feedback = _MemoryFeedback()
    feedback.rows[("acme/shop", _fingerprint("acme/shop", candidate), "u1")] = "wrong"

    original = ro.ReviewOrchestrator.__init__

    def with_feedback(self, provider=None, pack_builder=None, rule_source=None, feedback_source=None):
        original(self, provider, pack_builder, rule_source, feedback)

    monkeypatch.setattr(ro.ReviewOrchestrator, "__init__", with_feedback)
    result, _ = _run(tmp_path, FakeProvider([turn(call("submit_review", _REVIEW)), turn(call("submit_verdict", KEEP))]))
    review = result.review

    assert review.findings == []
    assert [(d.title, d.stage) for d in review.dropped if d.stage == "feedback"] == [("Negative totals possible", "feedback")]
