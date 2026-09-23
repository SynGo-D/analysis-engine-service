import re
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/repositories/{owner}/{repo}/analysis", tags=["analysis"])


@router.get("")
async def list_results(owner: str, repo: str, request: Request, limit: int = 20):
    """
    GET /api/repositories/{owner}/{repo}/analysis?limit=20

    Most-recent-first analysis results for a repository, one entry per PR
    run, each with its findings attached. `repository` is stored as
    "owner/repo" (matching PRJob/AnalysisJob), so the two path segments are
    rejoined before querying.
    """
    repository = f"{owner}/{repo}"
    results = await request.app.state.analysis_result_repository.list_for_repository(
        repository, limit=limit
    )
    return {"repository": repository, "results": results}


@router.get("/pull-requests/{pull_request_number}")
async def get_latest_for_pull_request(
    owner: str, repo: str, pull_request_number: int, request: Request,
    x_user_id: str | None = Header(default=None, max_length=100),
):
    """GET /api/repositories/{owner}/{repo}/analysis/pull-requests/{number} — latest run for one PR."""
    repository = f"{owner}/{repo}"
    result = await request.app.state.analysis_result_repository.get_latest_for_pull_request(
        repository, pull_request_number
    )
    if result is None:
        raise HTTPException(status_code=404, detail="No analysis result for this pull request yet.")
    await _attach_feedback(request, repository, result, x_user_id)
    return result


_FINGERPRINT = r"^[0-9a-f]{64}$"


class FeedbackBody(BaseModel):
    verdict: Literal["useful", "not_useful", "wrong"]
    note: str | None = Field(default=None, max_length=1000)


@router.put("/pull-requests/{pull_request_number}/review/findings/{fingerprint}/feedback", status_code=204)
async def give_feedback(
    owner: str, repo: str, pull_request_number: int, fingerprint: str, body: FeedbackBody, request: Request,
    x_user_id: str | None = Header(default=None, max_length=100),
):
    """
    PUT .../review/findings/{fingerprint}/feedback {verdict, note?}

    One verdict per user per issue; giving another replaces it. The user
    comes from the X-User-Id header, which main-backend sets from the
    verified session, never from the browser. An issue marked "wrong" is
    not reported again on later reviews of this repository.
    """
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Feedback needs a signed-in user.")
    if not re.fullmatch(_FINGERPRINT, fingerprint):
        raise HTTPException(status_code=422, detail="Invalid issue fingerprint.")
    await request.app.state.feedback_repository.record(
        f"{owner}/{repo}", fingerprint, x_user_id, body.verdict, body.note, pull_request_number
    )


async def _attach_feedback(request: Request, repository: str, result, user_id: str | None) -> None:
    feedback = getattr(request.app.state, "feedback_repository", None)
    if feedback is None or result.review is None or not result.review.findings:
        return
    summaries = await feedback.summaries(repository, [f.fingerprint for f in result.review.findings], user_id)
    for finding in result.review.findings:
        finding.feedback = summaries.get(finding.fingerprint)


usage_router = APIRouter(prefix="/api/repositories/{owner}/{repo}/review-usage", tags=["analysis"])


@usage_router.get("")
async def review_usage(owner: str, repo: str, request: Request, days: int = 30):
    """
    GET /api/repositories/{owner}/{repo}/review-usage?days=30

    AI review usage for one repository: reviews run (completed, failed,
    skipped), what they cost, issues reported, and developers' verdicts on
    them, including the share marked wrong: the real-world false-alarm rate.
    """
    if not 1 <= days <= 365:
        raise HTTPException(status_code=422, detail="days must be between 1 and 365.")
    return await request.app.state.feedback_repository.usage(f"{owner}/{repo}", days)
