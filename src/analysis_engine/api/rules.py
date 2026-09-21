import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError

from ..domain.business_rule import RULE_ID_PATTERN, BusinessRule, RuleSeverity, RuleStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repositories/{owner}/{repo}/rules", tags=["rules"])

# Repositories with a mining run in progress (this process). One at a time
# per repository: a second request would only spend twice on the same work.
_MINING: set[str] = set()


class NewRule(BaseModel):
    rule_id: str = Field(pattern=RULE_ID_PATTERN, max_length=40)
    rule: str = Field(min_length=5, max_length=500)
    applies_to: list[str] = Field(default_factory=list, max_length=20)
    severity: RuleSeverity = "medium"
    rationale: str | None = Field(default=None, max_length=500)


class RuleChange(BaseModel):
    rule: str | None = Field(default=None, min_length=5, max_length=500)
    applies_to: list[str] | None = Field(default=None, max_length=20)
    severity: RuleSeverity | None = None
    rationale: str | None = Field(default=None, max_length=500)
    # "active" accepts a suggestion; "rejected" keeps it out of reviews
    # (and out of future suggestions, since its id stays taken).
    status: RuleStatus | None = None


class MiningRequest(BaseModel):
    provider: Literal["github", "gitlab"] = "github"
    branch: str = Field(default="main", max_length=200)


@router.get("")
async def list_rules(owner: str, repo: str, request: Request, status: RuleStatus | None = None):
    """
    Rules stored for the repository: added here, or suggested and reviewed.
    Rules in the repository's own `.codepulse/rules.yml` aren't listed:
    they live with the code and are read at review time.
    """
    repository = f"{owner}/{repo}"
    rules = await request.app.state.business_rule_repository.list_rules(repository, status=status)
    return {"repository": repository, "rules": rules, "mining": repository in _MINING}


@router.post("", status_code=201)
async def add_rule(owner: str, repo: str, body: NewRule, request: Request):
    repository = f"{owner}/{repo}"
    store = request.app.state.business_rule_repository
    if await store.get(repository, body.rule_id) is not None:
        raise HTTPException(status_code=409, detail=f"Rule {body.rule_id} already exists.")
    try:
        rule = BusinessRule(**body.model_dump(), source="dashboard", status="active")
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()[0]["msg"]) from None
    await store.save(repository, rule)
    return rule


@router.patch("/{rule_id}")
async def change_rule(owner: str, repo: str, rule_id: str, body: RuleChange, request: Request):
    repository = f"{owner}/{repo}"
    store = request.app.state.business_rule_repository
    rule = await store.get(repository, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"No rule {rule_id}.")
    try:
        updated = BusinessRule(**(rule.model_dump() | body.model_dump(exclude_none=True)))
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()[0]["msg"]) from None
    await store.save(repository, updated)
    return updated


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(owner: str, repo: str, rule_id: str, request: Request):
    if not await request.app.state.business_rule_repository.delete(f"{owner}/{repo}", rule_id):
        raise HTTPException(status_code=404, detail=f"No rule {rule_id}.")
    return Response(status_code=204)


@router.post("/suggest", status_code=202)
async def suggest_rules(owner: str, repo: str, body: MiningRequest, request: Request):
    """
    Starts the Rule Miner in the background (it takes up to two minutes).
    Suggestions appear in GET .../rules with status "suggested".
    """
    miner = request.app.state.rule_miner
    if miner is None:
        raise HTTPException(status_code=503, detail="Rule suggestions need OPENAI_API_KEY to be configured.")
    repository = f"{owner}/{repo}"
    if repository in _MINING:
        raise HTTPException(status_code=409, detail="Suggestions are already being generated for this repository.")

    _MINING.add(repository)
    asyncio.create_task(_mine(request.app.state, miner, body, repository))
    return {"status": "started", "repository": repository}


async def _mine(state, miner, body: MiningRequest, repository: str) -> None:
    store = state.business_rule_repository
    try:
        existing = await store.list_rules(repository)
        result = await miner.mine_remote(body.provider, repository, body.branch, existing)
        inserted = await store.insert_suggestions(repository, result.suggestions)
        logger.info("rule mining for %s: %d stored, %d discarded, error=%s", repository, inserted,
                    len(result.discarded), result.error)
    except Exception:
        logger.exception("rule mining for %s failed", repository)
    finally:
        _MINING.discard(repository)
