from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

Provider = Literal["github", "gitlab"]


class AnalysisJob(BaseModel):
    """
    The job this service consumes from pr_queue.

    Field aliases match webhook-listener's PRJob message shape exactly
    (see that repo's src/messaging/PullRequestJobPublisher.ts) — there is
    no shared schema between the two services (different languages,
    separate repos), so this is the Python-side mirror of that JS
    interface, kept in sync by hand. `populate_by_name` lets this model
    also be constructed directly with the snake_case field names (useful
    in tests) rather than only via the camelCase aliases.
    """

    model_config = ConfigDict(populate_by_name=True)

    # Assigned internally, not part of the incoming message — job_id is
    # this service's own tracking ID (correlation IDs, idempotency in
    # Phase 10, log correlation), independent of anything webhook-listener
    # knows about. received_at is when *this* service picked the job up,
    # distinct from queued_at below (when webhook-listener queued it) —
    # the gap between the two is queue latency, worth keeping visible.
    job_id: UUID = Field(default_factory=uuid4)
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    provider: Provider
    repository: str = Field(description="owner/repo full name")
    clone_url: str = Field(alias="cloneUrl")
    commit_sha: str = Field(alias="commit")
    branch: str
    pull_request_number: int = Field(alias="prNumber")
    queued_at: str = Field(alias="timestamp", description="When webhook-listener queued this job")
