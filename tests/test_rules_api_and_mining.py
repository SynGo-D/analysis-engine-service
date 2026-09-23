import asyncio
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from analysis_engine.api import rules as rules_api
from analysis_engine.domain.business_rule import BusinessRule
from analysis_engine.rules.mining import RuleMiner

from .fake_provider import FakeProvider, call, turn


class _MemoryStore:
    def __init__(self):
        self.rules: dict[tuple[str, str], BusinessRule] = {}

    async def list_rules(self, repository, status=None):
        return [r for (repo, _), r in self.rules.items() if repo == repository and (status is None or r.status == status)]

    async def get(self, repository, rule_id):
        return self.rules.get((repository, rule_id))

    async def save(self, repository, rule):
        self.rules[(repository, rule.rule_id)] = rule

    async def delete(self, repository, rule_id):
        return self.rules.pop((repository, rule_id), None) is not None

    async def insert_suggestions(self, repository, rules):
        new = [r for r in rules if (repository, r.rule_id) not in self.rules]
        for rule in new:
            self.rules[(repository, rule.rule_id)] = rule
        return len(new)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(rules_api.router)
    app.state.business_rule_repository = _MemoryStore()
    app.state.rule_miner = None
    return TestClient(app)


BASE = "/api/repositories/acme/shop/rules"


def test_add_list_edit_and_delete_a_rule(client):
    created = client.post(BASE, json={"rule_id": "REFUND-APPROVAL", "rule": "Refunds over $500 need a manager.",
                                      "applies_to": ["payments/**"], "severity": "high"})
    assert created.status_code == 201 and created.json()["source"] == "dashboard"

    assert [r["rule_id"] for r in client.get(BASE).json()["rules"]] == ["REFUND-APPROVAL"]

    edited = client.patch(f"{BASE}/REFUND-APPROVAL", json={"severity": "medium"})
    assert edited.json()["severity"] == "medium" and edited.json()["rule"] == "Refunds over $500 need a manager."

    assert client.delete(f"{BASE}/REFUND-APPROVAL").status_code == 204
    assert client.get(BASE).json()["rules"] == []


def test_accepting_a_suggestion_makes_it_active(client):
    store = client.app.state.business_rule_repository
    asyncio.run(store.save("acme/shop", BusinessRule(rule_id="PII-LOGGING", rule="Never log emails.",
                                                      source="suggested", status="suggested")))

    client.patch(f"{BASE}/PII-LOGGING", json={"status": "active"})

    assert [r["rule_id"] for r in client.get(BASE, params={"status": "active"}).json()["rules"]] == ["PII-LOGGING"]


@pytest.mark.parametrize("body", [
    {"rule_id": "lower-case", "rule": "Some rule text."},
    {"rule_id": "OK-ID", "rule": "x"},
    {"rule_id": "OK-ID", "rule": "Scope escapes.", "applies_to": ["../secrets/**"]},
])
def test_rejects_invalid_rules(client, body):
    assert client.post(BASE, json=body).status_code == 422


def test_duplicate_ids_and_missing_rules(client):
    body = {"rule_id": "DUP", "rule": "First version."}
    client.post(BASE, json=body)

    assert client.post(BASE, json=body).status_code == 409
    assert client.patch(f"{BASE}/NOPE", json={"severity": "low"}).status_code == 404
    assert client.delete(f"{BASE}/NOPE").status_code == 404


def test_suggestions_need_a_configured_model(client):
    assert client.post(f"{BASE}/suggest", json={}).status_code == 503


# ---------------------------------------------------------------------------
# The Rule Miner's output checks
# ---------------------------------------------------------------------------

_REPO_FILES = {
    "README.md": "# Shop\n\nRefunds above 500 dollars must be approved by a manager.\n",
    "payments/refunds.py": 'def refund(amount, approved_by=None):\n    if amount > 500 and not approved_by:\n        raise PermissionError("refunds above 500 must be approved")\n',
    "tests/test_refunds.py": "def test_large_refund_needs_approval():\n    pass\n",
}


def _checkout(tmp_path):
    for name, content in _REPO_FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "x"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, env=env, capture_output=True)
    return tmp_path


def _suggestion(rule_id, quote, applies_to=("payments/**",), ref="payments/refunds.py:2"):
    return {"rule_id": rule_id, "rule": "Refunds above 500 dollars need a manager's approval.",
            "applies_to": list(applies_to), "severity": "high", "rationale": "Fraud control.",
            "source": {"type": "code_location", "ref": ref, "quote": quote}}


def _mine(tmp_path, suggestions, existing=()):
    provider = FakeProvider([turn(call("submit_rules", {"suggestions": suggestions}))])
    result = asyncio.run(RuleMiner(provider).mine(_checkout(tmp_path), list(existing)))
    return result, provider


def test_keeps_suggestions_whose_source_is_really_there(tmp_path):
    result, provider = _mine(tmp_path, [_suggestion("REFUND-APPROVAL", "if amount > 500 and not approved_by:")])

    rule = result.suggestions[0]
    assert (rule.rule_id, rule.status, rule.source) == ("REFUND-APPROVAL", "suggested", "suggested")
    assert "if amount > 500" in rule.evidence
    # The Miner was shown the repository's own statements of its rules.
    brief = provider.requests[0].items[0]["content"]
    assert "must be approved by a manager" in brief and "test_large_refund_needs_approval" in brief


def test_discards_invented_sources_unknown_scopes_and_taken_ids(tmp_path):
    existing = [BusinessRule(rule_id="TAKEN", rule="Already a rule here.", source="dashboard")]
    result, _ = _mine(tmp_path, [
        _suggestion("INVENTED", "if amount > 1000:"),
        _suggestion("NO-SUCH-SCOPE", "if amount > 500 and not approved_by:", applies_to=["billing/**"]),
        _suggestion("TAKEN", "if amount > 500 and not approved_by:"),
    ], existing)

    assert result.suggestions == []
    assert [d.split(":")[0] for d in result.discarded] == ["INVENTED", "NO-SUCH-SCOPE", "TAKEN"]
