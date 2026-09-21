from ..domain import AgentFinding
from ..domain.agent_review import RiskLevel

# At most this many issues are reported per PR (principle 4, §10.2):
# a short list gets read; a long one gets ignored.
MAX_REPORTED = 7

_SEVERITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}


def rank_and_cap(findings: list[AgentFinding]) -> list[AgentFinding]:
    """
    Merges issues that describe the same place, ranks by severity x
    confidence, and keeps the top MAX_REPORTED.

    Plain code, not an agent: ordering and capping are rules, and a rule
    is free, instant and testable.
    """
    merged: list[AgentFinding] = []
    for finding in sorted(findings, key=_score, reverse=True):
        duplicate = next((m for m in merged if _same_place(m, finding)), None)
        if duplicate is None:
            merged.append(finding)
        else:
            # Keep the stronger issue; add the weaker one's evidence to it.
            known = {(e.type, e.ref) for e in duplicate.evidence}
            duplicate.evidence.extend(e for e in finding.evidence if (e.type, e.ref) not in known)
    return merged[:MAX_REPORTED]


def risk_level(findings: list[AgentFinding]) -> RiskLevel:
    severities = {f.severity for f in findings}
    if "high" in severities:
        return "high"
    if "medium" in severities:
        return "medium"
    return "low"


def _score(finding: AgentFinding) -> float:
    return _SEVERITY_WEIGHT[finding.severity] * finding.confidence


def _same_place(a: AgentFinding, b: AgentFinding) -> bool:
    return (
        a.file_path == b.file_path
        and a.category == b.category
        and a.line_start <= b.line_end
        and b.line_start <= a.line_end
    )
