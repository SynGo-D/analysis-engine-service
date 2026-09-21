import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# BR-PRICING-001, REFUND-LIMIT, PII-2 ... Upper-case, so a rule id is easy
# to tell apart from code when a model cites it.
RULE_ID_PATTERN = r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$"

RuleSource = Literal["repository_file", "dashboard", "suggested"]
RuleStatus = Literal["active", "suggested", "rejected"]
RuleSeverity = Literal["high", "medium", "low"]


class BusinessRule(BaseModel):
    """
    One of a repository's own business rules (docs/agent-architecture.md §9).

    Rules are what let a review say "this breaks *your* rule", which no
    generic linter or model can know. Every issue that claims a rule
    violation must cite the rule's id, and the engine checks the id exists.
    """

    rule_id: str = Field(pattern=RULE_ID_PATTERN, max_length=40)
    rule: str = Field(min_length=5, max_length=500)
    # Glob patterns over repository paths. Empty = applies to every PR.
    applies_to: list[str] = Field(default_factory=list, max_length=20)
    # The highest severity an issue citing this rule can have.
    severity: RuleSeverity = "medium"
    rationale: str | None = Field(default=None, max_length=500)

    source: RuleSource = "repository_file"
    status: RuleStatus = "active"
    # For suggested rules: where in the repository the Rule Miner found it.
    evidence: str | None = Field(default=None, max_length=700)

    @field_validator("applies_to")
    @classmethod
    def _valid_globs(cls, patterns: list[str]) -> list[str]:
        # removeprefix, not lstrip: lstrip("./") strips any leading dots and
        # slashes, silently turning "../secrets/**" into "secrets/**".
        cleaned = [p.strip().removeprefix("./") for p in patterns if p and p.strip()]
        for pattern in cleaned:
            if len(pattern) > 200 or pattern.startswith("/") or ".." in pattern.split("/"):
                raise ValueError(f"invalid path pattern: {pattern!r}")
        return cleaned

    def applies_to_path(self, path: str) -> bool:
        return not self.applies_to or any(glob_match(path, pattern) for pattern in self.applies_to)


def glob_match(path: str, pattern: str) -> bool:
    """
    Repository-path globbing: `*` stays within one directory, `**` spans
    any number of them, `?` is one character. "src/billing/**" matches
    everything under src/billing; "**/*.sql" matches SQL files anywhere.
    """
    return _compile(pattern).fullmatch(path) is not None


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    regex = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            regex.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            regex.append(".*")
            i += 2
        elif pattern[i] == "*":
            regex.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            regex.append("[^/]")
            i += 1
        else:
            regex.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(regex))
