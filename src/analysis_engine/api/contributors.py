from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/repositories/{owner}/{repo}/contributors", tags=["contributors"])


class ContributorDebt(BaseModel):
    """
    Technical debt introduced by one person.

    A placeholder with a real shape. The debt calculation service does not
    exist yet, so `status` is "pending" and `score` is null for everyone —
    but the field is here now so that service can fill it without the API
    or the page changing. A missing field would have meant a second round
    of changes across three services.

    `score` is deliberately unitless here: what it counts (remediation
    minutes, a weighted index, money) is the debt service's decision, and
    guessing now would bake in the wrong one.
    """

    score: float | None = None
    status: str = Field(default="pending", description='"pending" until the debt service reports, then "available"')
    introduced_at: str | None = None


class ContributorReviewFindings(BaseModel):
    """AI review issues raised on this person's pull requests, by severity."""

    high: int = 0
    medium: int = 0
    low: int = 0

    @property
    def total(self) -> int:
        return self.high + self.medium + self.low


class Contributor(BaseModel):
    username: str
    provider_user_id: str | None = None

    pull_requests: int = 0
    analyses: int = 0
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0

    # Linter findings across this person's pull requests.
    issues: int = 0
    errors: int = 0
    warnings: int = 0

    review_findings: ContributorReviewFindings = Field(default_factory=ContributorReviewFindings)
    debt: ContributorDebt = Field(default_factory=ContributorDebt)

    last_analysis_at: str | None = None


@router.get("")
async def list_contributors(owner: str, repo: str, request: Request):
    """
    GET /api/repositories/{owner}/{repo}/contributors

    Everyone who has opened an analysed pull request in this repository,
    with what their work amounted to, busiest first.

    Built entirely from analyses this platform ran — not from the
    provider's contributor list. The two answer different questions: the
    provider knows who committed, this knows whose pull requests were
    reviewed and what was found in them. A contributor who has never
    opened a pull request since the repository was connected therefore
    does not appear, which is correct: there is nothing to report about
    them.

    People whose analyses predate author capture are absent for the same
    reason — listing them as "unknown" would read as a person.
    """
    repository = f"{owner}/{repo}"
    repo_store = request.app.state.analysis_result_repository

    summaries = await repo_store.contributor_summary(repository)
    review_findings = await repo_store.contributor_review_findings(repository)

    contributors = [
        Contributor(
            username=row["username"],
            provider_user_id=row["provider_user_id"],
            pull_requests=row["pull_requests"],
            analyses=row["analyses"],
            files_changed=row["files_changed"],
            lines_added=row["lines_added"],
            lines_removed=row["lines_removed"],
            issues=row["issues"],
            errors=row["errors"],
            warnings=row["warnings"],
            review_findings=ContributorReviewFindings(
                **{
                    severity: count
                    for severity, count in review_findings.get(row["username"], {}).items()
                    if severity in {"high", "medium", "low"}
                }
            ),
            last_analysis_at=row["last_analysis_at"].isoformat() if row["last_analysis_at"] else None,
        )
        for row in summaries
    ]

    return {
        "repository": repository,
        "contributors": contributors,
        # Stated rather than implied: the page says so, so nobody reads a
        # blank debt column as "no debt".
        "debt_source": "pending",
    }
