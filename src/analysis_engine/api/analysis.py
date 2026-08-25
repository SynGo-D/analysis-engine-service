from fastapi import APIRouter, HTTPException, Request

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
    owner: str, repo: str, pull_request_number: int, request: Request
):
    """GET /api/repositories/{owner}/{repo}/analysis/pull-requests/{number} — latest run for one PR."""
    repository = f"{owner}/{repo}"
    result = await request.app.state.analysis_result_repository.get_latest_for_pull_request(
        repository, pull_request_number
    )
    if result is None:
        raise HTTPException(status_code=404, detail="No analysis result for this pull request yet.")
    return result
