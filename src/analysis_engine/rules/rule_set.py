import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import ValidationError

from ..config import settings
from ..domain import ChangeSet
from ..domain.business_rule import BusinessRule
from ..workspace import WorkspaceSecurityError, run_git_output

logger = logging.getLogger(__name__)

RULES_FILES = (".codepulse/rules.yml", ".codepulse/rules.yaml")
MAX_RULES_FILE_BYTES = 64_000
MAX_RULES = 200


@dataclass
class RuleSet:
    """The business rules in force for one review."""

    rules: list[BusinessRule] = field(default_factory=list)
    # Problems with the rules file, e.g. one malformed rule. Reported, and
    # the rest still apply: one typo must not switch every rule off.
    errors: list[str] = field(default_factory=list)
    # The PR edits the rules file. Rules still come from the target branch
    # (a PR can't weaken the rules it's reviewed against), but the Reviewer
    # is told, so it can mention it.
    modified_by_pr: bool = False

    def get(self, rule_id: str) -> BusinessRule | None:
        rule_id = rule_id.strip().strip("[]").strip()
        return next((r for r in self.rules if r.rule_id == rule_id), None)

    def applicable(self, change_set: ChangeSet) -> list[BusinessRule]:
        paths = [f.path for f in change_set.files if f.status != "deleted"]
        return [r for r in self.rules if any(r.applies_to_path(p) for p in paths)]


class RuleSource(Protocol):
    async def active_rules(self, repository: str) -> list[BusinessRule]: ...


def parse_rules_file(text: str) -> tuple[list[BusinessRule], list[str]]:
    """
    Parses `.codepulse/rules.yml`:

        version: 1
        rules:
          - id: BR-PRICING-001
            rule: Discounts are applied before tax, never after.
            applies_to: ["src/billing/**"]
            severity: high
            rationale: Tax is owed on the discounted price.

    yaml.safe_load only: the file is written by whoever can push to the
    repository, and full YAML loading can construct arbitrary objects.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return [], [f"rules file is not valid YAML: {str(error).splitlines()[0]}"]

    entries = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return [], ["rules file must have a top-level 'rules' list"]

    rules: list[BusinessRule] = []
    errors: list[str] = []
    seen: set[str] = set()
    for position, entry in enumerate(entries[:MAX_RULES], start=1):
        if not isinstance(entry, dict):
            errors.append(f"rule #{position}: not a mapping")
            continue
        try:
            rule = BusinessRule(
                rule_id=str(entry.get("id", "")).strip(),
                rule=str(entry.get("rule", "")).strip(),
                applies_to=entry.get("applies_to") or [],
                severity=entry.get("severity", "medium"),
                rationale=entry.get("rationale"),
                source="repository_file",
            )
        except (ValidationError, TypeError) as error:
            errors.append(f"rule #{position} ({entry.get('id', 'no id')}): {_first_problem(error)}")
            continue
        if rule.rule_id in seen:
            errors.append(f"rule #{position}: duplicate id {rule.rule_id}")
            continue
        seen.add(rule.rule_id)
        rules.append(rule)

    if len(entries) > MAX_RULES:
        errors.append(f"only the first {MAX_RULES} rules are used")
    return rules, errors


async def load_rules(
    workspace: Path,
    repository: str,
    change_set: ChangeSet,
    stored_rules: RuleSource | None = None,
) -> RuleSet:
    """
    The rules for one review: the repository's rules file *as it is on the
    target branch*, plus active rules stored for the repository (added in
    the dashboard, or suggested and accepted). On an id clash the file
    wins: it's versioned and reviewed like code.
    """
    result = RuleSet(modified_by_pr=any(change_set.file(name) is not None for name in RULES_FILES))

    text = await _rules_file_on(workspace, change_set.target_branch)
    if text is not None:
        result.rules, result.errors = parse_rules_file(text)

    if stored_rules is not None:
        try:
            stored = await stored_rules.active_rules(repository)
        except Exception:
            logger.exception("could not load stored rules for %s; using the rules file only", repository)
            stored = []
        known = {r.rule_id for r in result.rules}
        result.rules.extend(r for r in stored if r.rule_id not in known)

    return result


async def _rules_file_on(workspace: Path, branch: str) -> str | None:
    # DiffExtractor already fetched the target branch into this ref.
    ref = f"refs/remotes/origin/{branch}"
    for name in RULES_FILES:
        try:
            code, output = await run_git_output(
                ["show", f"{ref}:{name}"], workspace, settings.git_clone_timeout_seconds, allowed_exit_codes=(0, 128)
            )
        except WorkspaceSecurityError:
            return None
        if code == 0:
            if len(output) > MAX_RULES_FILE_BYTES:
                logger.warning("%s on %s is over %d bytes; ignored", name, branch, MAX_RULES_FILE_BYTES)
                return None
            return output.decode("utf-8", errors="replace")
    return None


def _first_problem(error: Exception) -> str:
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        field_name = {"rule_id": "id"}.get(str(first["loc"][0]), str(first["loc"][0])) if first["loc"] else ""
        return f"{field_name}: {first['msg']}"
    return str(error)[:200]
