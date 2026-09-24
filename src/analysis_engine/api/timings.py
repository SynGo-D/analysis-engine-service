from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/timings", tags=["timings"])


@router.get("")
async def timings(request: Request, days: int = 7, repository: str | None = None):
    """
    GET /api/timings?days=7[&repository=owner/repo]

    Where a review's time goes, as p50 and p95 per pipeline stage.

    This is an operational view, not a tenant's: it answers "should we
    cache the repository scan" and "is the Verifier worth its latency",
    which are questions about the platform rather than about anyone's
    code. It reports durations and counts only — no repository contents,
    no findings, no authorship — and `repository` narrows it when a
    specific one looks slow.

    Stage names come from the stored data rather than a list here, so a
    stage added to the pipeline appears without changing this endpoint.
    """
    days = max(1, min(days, 90))

    return await request.app.state.analysis_result_repository.timing_percentiles(
        repository, days
    )
