import asyncio

import pytest

from analysis_engine.agents.reviewer import ReviewerOutput, validate_review
from analysis_engine.context import ContextPackBuilder
from analysis_engine.diffing import DiffExtractor, changed_symbols
from analysis_engine.domain.business_rule import BusinessRule, glob_match
from analysis_engine.indexing import CodeIndexer
from analysis_engine.retrieval import RetrievalTools, ToolExecutor
from analysis_engine.review.review_orchestrator import _rule_checks
from analysis_engine.rules import load_rules, parse_rules_file

from .test_context_pack import _DISCOUNT_FIX, _JOB, _setup

RULES_YML = """version: 1
rules:
  - id: BR-PRICING-001
    rule: Discounts are applied before tax, never after.
    applies_to: ["billing/**"]
    severity: high
    rationale: Tax is owed on the discounted price.
  - id: PII-LOGGING
    rule: Customer email addresses must never be written to logs.
    severity: medium
  - id: REPORTS-ONLY
    rule: Reports show amounts in the account's currency.
    applies_to: ["reports/**"]
"""


# ---------------------------------------------------------------------------
# The rules file
# ---------------------------------------------------------------------------


def test_parses_a_rules_file():
    rules, errors = parse_rules_file(RULES_YML)

    assert errors == []
    assert [r.rule_id for r in rules] == ["BR-PRICING-001", "PII-LOGGING", "REPORTS-ONLY"]
    assert rules[0].applies_to == ["billing/**"] and rules[0].severity == "high"


def test_one_bad_rule_is_reported_and_the_rest_still_apply():
    text = RULES_YML + """  - id: lowercase-id
    rule: Bad id.
  - id: PII-LOGGING
    rule: A duplicate id.
  - rule: No id at all.
"""
    rules, errors = parse_rules_file(text)

    assert [r.rule_id for r in rules] == ["BR-PRICING-001", "PII-LOGGING", "REPORTS-ONLY"]
    assert len(errors) == 3
    assert any("duplicate id PII-LOGGING" in e for e in errors)


@pytest.mark.parametrize("text", ["not: [valid", "just a string", "rules: {}"])
def test_an_unreadable_file_gives_no_rules_and_an_error(text):
    rules, errors = parse_rules_file(text)

    assert rules == [] and len(errors) == 1


def test_yaml_tags_that_construct_objects_are_refused():
    rules, errors = parse_rules_file("rules: !!python/object/apply:os.system ['echo hacked']")

    assert rules == [] and "not valid YAML" in errors[0]


@pytest.mark.parametrize("path, pattern, expected", [
    ("billing/total.py", "billing/**", True),
    ("billing/sub/deep.py", "billing/**", True),
    ("billing/total.py", "billing/*.py", True),
    ("billing/sub/deep.py", "billing/*.py", False),
    ("src/db/schema.sql", "**/*.sql", True),
    ("schema.sql", "**/*.sql", True),
    ("reports/x.py", "billing/**", False),
])
def test_glob_scopes(path, pattern, expected):
    assert glob_match(path, pattern) is expected


# ---------------------------------------------------------------------------
# Loading rules for a review
# ---------------------------------------------------------------------------


def _changes(workspace):
    return asyncio.run(DiffExtractor(timeout=30).extract(workspace, _JOB))


def test_rules_come_from_the_target_branch_so_a_pr_cant_weaken_them(tmp_path):
    weakened = dict(_DISCOUNT_FIX)
    weakened[".codepulse/rules.yml"] = "version: 1\nrules: []\n"     # the PR deletes every rule
    workspace = _setup(tmp_path, weakened, base_files={".codepulse/rules.yml": RULES_YML})
    changes = _changes(workspace)

    rules = asyncio.run(load_rules(workspace, "acme/shop", changes.change_set))

    assert [r.rule_id for r in rules.rules] == ["BR-PRICING-001", "PII-LOGGING", "REPORTS-ONLY"]
    assert rules.modified_by_pr is True


def test_only_rules_whose_scope_matches_a_changed_file_apply(tmp_path):
    workspace = _setup(tmp_path, _DISCOUNT_FIX, base_files={".codepulse/rules.yml": RULES_YML})
    changes = _changes(workspace)
    rules = asyncio.run(load_rules(workspace, "acme/shop", changes.change_set))

    # billing/total.py changed: the billing rule and the everywhere-rule apply, reports doesn't.
    assert [r.rule_id for r in rules.applicable(changes.change_set)] == ["BR-PRICING-001", "PII-LOGGING"]


class _Stored:
    def __init__(self, rules):
        self.rules = rules

    async def active_rules(self, repository):
        return self.rules


def test_stored_rules_are_added_and_the_file_wins_on_a_clash(tmp_path):
    workspace = _setup(tmp_path, _DISCOUNT_FIX, base_files={".codepulse/rules.yml": RULES_YML})
    stored = _Stored([
        BusinessRule(rule_id="PII-LOGGING", rule="A dashboard version of the same id.", source="dashboard"),
        BusinessRule(rule_id="REFUND-APPROVAL", rule="Refunds over $500 need a manager.", source="dashboard"),
    ])

    rules = asyncio.run(load_rules(workspace, "acme/shop", _changes(workspace).change_set, stored))

    assert rules.get("PII-LOGGING").rule.startswith("Customer email")
    assert rules.get("REFUND-APPROVAL").source == "dashboard"


def test_a_repository_without_a_rules_file_simply_has_no_rules(tmp_path):
    workspace = _setup(tmp_path, _DISCOUNT_FIX)

    rules = asyncio.run(load_rules(workspace, "acme/shop", _changes(workspace).change_set))

    assert rules.rules == [] and rules.errors == []


# ---------------------------------------------------------------------------
# What the agents see
# ---------------------------------------------------------------------------


def _loaded(tmp_path, head=_DISCOUNT_FIX):
    workspace = _setup(tmp_path, head, base_files={".codepulse/rules.yml": RULES_YML})
    changes = _changes(workspace)
    index = CodeIndexer().build(workspace)
    changes.changed_symbols = changed_symbols(index, changes.change_set)
    rules = asyncio.run(load_rules(workspace, "acme/shop", changes.change_set))
    return workspace, changes, index, rules


def test_the_context_pack_lists_applicable_rules_right_after_the_pr(tmp_path):
    workspace, changes, index, rules = _loaded(tmp_path)

    pack = asyncio.run(ContextPackBuilder(timeout=30).build(workspace, _JOB, changes, index, [], rules))

    assert [s.title for s in pack.sections][:2] == ["Pull request", "Business rules"]
    body = pack.sections[1].body
    assert "[BR-PRICING-001] (high) Discounts are applied before tax" in body
    assert "REPORTS-ONLY" not in body


def test_get_rule_tool(tmp_path):
    workspace, _changes_, index, rules = _loaded(tmp_path)
    executor = ToolExecutor(RetrievalTools(workspace, index, [], rules=rules))

    assert "Tax is owed on the discounted price" in asyncio.run(executor.execute("get_rule", {"rule_id": "BR-PRICING-001"}))
    assert asyncio.run(executor.execute("get_rule", {"rule_id": "NOPE"})).startswith("Error: No rule 'NOPE'")


# ---------------------------------------------------------------------------
# Validating rule claims
# ---------------------------------------------------------------------------

_QUOTE = {"type": "code_location", "ref": "billing/total.py:2", "quote": "return (price - discount) * 1.2"}


def _rule_candidate(evidence, severity="high", category="business_rule"):
    return {"title": "Rule broken", "category": category, "severity": severity, "confidence": 0.9,
            "file_path": "billing/total.py", "line_start": 2, "line_end": 2, "explanation": "e",
            "evidence": evidence, "suggested_fix": None}


def _validate(tmp_path, candidates, rule_checks=()):
    workspace, _c, _i, rules = _loaded(tmp_path)
    output = ReviewerOutput.model_validate({"summary": "s", "areas_touched": [], "linter_triage": [],
                                            "candidates": candidates, "rule_checks": list(rule_checks)})
    return validate_review(output, workspace, [], "acme/shop", rules), rules


def test_a_rule_violation_citing_a_real_rule_and_real_code_is_kept(tmp_path):
    result, _ = _validate(tmp_path, [_rule_candidate([{"type": "business_rule", "ref": "BR-PRICING-001", "quote": None}, _QUOTE])])

    assert result.findings[0].rule_ids == ["BR-PRICING-001"]


def test_an_invented_rule_is_dropped(tmp_path):
    result, _ = _validate(tmp_path, [_rule_candidate([{"type": "business_rule", "ref": "BR-MADE-UP", "quote": None}, _QUOTE])])

    assert result.findings == [] and "business rule that doesn't exist" in result.dropped[0].reason


def test_a_rule_violation_must_cite_the_rule(tmp_path):
    result, _ = _validate(tmp_path, [_rule_candidate([_QUOTE])])

    assert result.findings == [] and "without citing the rule" in result.dropped[0].reason


def test_citing_a_rule_alone_does_not_prove_a_violation(tmp_path):
    result, _ = _validate(tmp_path, [_rule_candidate([{"type": "business_rule", "ref": "BR-PRICING-001", "quote": None}])])

    assert result.findings == []


def test_a_rules_severity_caps_the_issue(tmp_path):
    evidence = [{"type": "business_rule", "ref": "PII-LOGGING", "quote": None}, _QUOTE]    # PII-LOGGING is medium

    result, _ = _validate(tmp_path, [_rule_candidate(evidence, severity="high")])

    assert result.findings[0].severity == "medium"


def test_rule_outcomes_need_a_surviving_issue_to_count_as_violated(tmp_path):
    workspace, changes, _i, rules = _loaded(tmp_path)
    output = ReviewerOutput.model_validate({
        "summary": "s", "areas_touched": [], "linter_triage": [],
        "candidates": [_rule_candidate([{"type": "business_rule", "ref": "BR-PRICING-001", "quote": None}, _QUOTE])],
        "rule_checks": [{"rule_id": "BR-PRICING-001", "outcome": "violated", "note": "after tax"},
                        {"rule_id": "PII-LOGGING", "outcome": "violated", "note": "logs email"}],
    })
    validated = validate_review(output, workspace, [], "acme/shop", rules)

    checks = {c.rule_id: c.outcome for c in _rule_checks(rules, changes.change_set, output.rule_checks, validated.findings)}

    # BR-PRICING-001 has a surviving issue that cites it; PII-LOGGING was only claimed.
    assert checks == {"BR-PRICING-001": "violated", "PII-LOGGING": "not_confirmed"}


def test_rules_the_reviewer_skipped_are_marked_not_checked(tmp_path):
    _w, changes, _i, rules = _loaded(tmp_path)

    checks = _rule_checks(rules, changes.change_set, [], [])

    assert {c.outcome for c in checks} == {"not_checked"}
