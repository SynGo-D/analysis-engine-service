from collections import defaultdict

from ...domain import (
    BanditMetrics,
    Finding,
    HalsteadMetrics,
    MaintainabilityMetrics,
    PylintMetrics,
    PythonLoc,
    PythonMetrics,
    RadonComplexityEntry,
    RadonComplexityMetrics,
    RadonHalsteadEntry,
    RadonMaintainabilityEntry,
    RadonRawLocEntry,
)

# The single source of truth for "what counts as high complexity /
# low maintainability" — analyzers/python/radon_analyzer.py imports these
# rather than redefining its own copies, so the Findings it creates and
# the aggregate counts here can never drift apart.
HIGH_COMPLEXITY_THRESHOLD = 11
LOW_MAINTAINABILITY_THRESHOLD = 65.0


def _count_by(findings: list[Finding], key) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for finding in findings:
        counts[key(finding)] += 1
    return dict(counts)


def calculate_python_loc(raw_loc_entries: list[RadonRawLocEntry]) -> PythonLoc:
    """
    The single source of Python LOC/KLOC — built from Radon's own `raw`
    command output (see analyzers/python/radon_analyzer.py), never
    recounted independently, per the explicit instruction to reuse
    Radon's LOC data rather than duplicating it.
    """
    if not raw_loc_entries:
        return PythonLoc()

    physical = sum(e.loc for e in raw_loc_entries)
    return PythonLoc(
        physical_loc=physical,
        logical_loc=sum(e.lloc for e in raw_loc_entries),
        source_loc=sum(e.sloc for e in raw_loc_entries),
        comment_loc=sum(e.comments + e.multi + e.single_comments for e in raw_loc_entries),
        blank_loc=sum(e.blank for e in raw_loc_entries),
        kloc=physical / 1000 if physical else 0.0,
    )


def calculate_pylint_metrics(findings: list[Finding], kloc: float = 0.0) -> PylintMetrics:
    by_type = _count_by(findings, lambda f: f.metadata.get("pylintType", "warning"))

    return PylintMetrics(
        total_issues=len(findings),
        errors=by_type.get("error", 0),
        warnings=by_type.get("warning", 0),
        conventions=by_type.get("convention", 0),
        refactors=by_type.get("refactor", 0),
        fatals=by_type.get("fatal", 0),
        issues_per_kloc=round(len(findings) / kloc, 2) if kloc else 0.0,
        issues_by_file=_count_by(findings, lambda f: f.file_path),
        issues_by_category=_count_by(findings, lambda f: f.category),
        issues_by_rule=_count_by(findings, lambda f: f.rule_id),
    )


def calculate_radon_complexity_metrics(
    entries: list[RadonComplexityEntry], kloc: float = 0.0
) -> RadonComplexityMetrics:
    if not entries:
        return RadonComplexityMetrics()

    complexities = [e.complexity for e in entries]
    distribution: dict[str, int] = defaultdict(int)
    for entry in entries:
        distribution[entry.rank] += 1

    total = sum(complexities)
    return RadonComplexityMetrics(
        total_complexity=total,
        average_complexity=round(total / len(entries), 2),
        maximum_complexity=max(complexities),
        complexity_distribution=dict(distribution),
        high_complexity_function_count=sum(1 for c in complexities if c >= HIGH_COMPLEXITY_THRESHOLD),
        complexity_per_kloc=round(total / kloc, 2) if kloc else 0.0,
    )


def calculate_maintainability_metrics(entries: list[RadonMaintainabilityEntry]) -> MaintainabilityMetrics:
    if not entries:
        return MaintainabilityMetrics()

    values = [e.mi for e in entries]
    return MaintainabilityMetrics(
        average_maintainability_index=round(sum(values) / len(values), 2),
        minimum_maintainability_index=round(min(values), 2),
        maximum_maintainability_index=round(max(values), 2),
        low_maintainability_file_count=sum(1 for v in values if v < LOW_MAINTAINABILITY_THRESHOLD),
    )


def calculate_halstead_metrics(entries: list[RadonHalsteadEntry]) -> HalsteadMetrics:
    """Best-effort — only entries with a real (non-empty-file) total contribute, per section 4's "where supported"."""
    volumes = [e.total.volume for e in entries]
    if not volumes:
        return HalsteadMetrics()

    difficulties = [e.total.difficulty for e in entries]
    efforts = [e.total.effort for e in entries]

    return HalsteadMetrics(
        total_volume=round(sum(volumes), 2),
        average_volume=round(sum(volumes) / len(volumes), 2),
        average_difficulty=round(sum(difficulties) / len(difficulties), 2),
        average_effort=round(sum(efforts) / len(efforts), 2),
    )


def calculate_bandit_metrics(findings: list[Finding], kloc: float = 0.0) -> BanditMetrics:
    # Excludes bandit-parse-error findings (category "bug", not a security
    # finding) — those come from the same tool run but aren't a security
    # issue count.
    security_findings = [f for f in findings if f.tool == "bandit" and f.category == "vulnerability"]

    by_severity = _count_by(security_findings, lambda f: f.severity)
    high_confidence = sum(1 for f in security_findings if f.metadata.get("confidence") == "HIGH")

    return BanditMetrics(
        total_issues=len(security_findings),
        high_severity=by_severity.get("error", 0),
        medium_severity=by_severity.get("warning", 0),
        low_severity=by_severity.get("info", 0),
        high_confidence=high_confidence,
        issues_per_kloc=round(len(security_findings) / kloc, 2) if kloc else 0.0,
        issues_by_rule=_count_by(security_findings, lambda f: f.rule_id),
        issues_by_file=_count_by(security_findings, lambda f: f.file_path),
    )


def calculate_python_metrics(
    loc: PythonLoc,
    pylint_metrics: PylintMetrics,
    complexity_metrics: RadonComplexityMetrics,
    maintainability_metrics: MaintainabilityMetrics,
    halstead_metrics: HalsteadMetrics,
    bandit_metrics: BanditMetrics,
) -> PythonMetrics:
    """
    The combined, cross-tool aggregate — section 7's "Python-level
    abstract metrics". Takes each tool's own already-computed metrics
    (all of which were recomputed with the real `kloc` by this point, see
    PythonAnalyzer) rather than recalculating anything from findings
    itself.
    """
    return PythonMetrics(
        loc=loc,
        pylint_issue_count=pylint_metrics.total_issues,
        pylint_issue_density=pylint_metrics.issues_per_kloc,
        average_cyclomatic_complexity=complexity_metrics.average_complexity,
        maximum_cyclomatic_complexity=complexity_metrics.maximum_complexity,
        high_complexity_function_count=complexity_metrics.high_complexity_function_count,
        average_maintainability_index=maintainability_metrics.average_maintainability_index,
        minimum_maintainability_index=maintainability_metrics.minimum_maintainability_index,
        low_maintainability_file_count=maintainability_metrics.low_maintainability_file_count,
        bandit_issue_count=bandit_metrics.total_issues,
        high_severity_security_issue_count=bandit_metrics.high_severity,
        medium_severity_security_issue_count=bandit_metrics.medium_severity,
        low_severity_security_issue_count=bandit_metrics.low_severity,
        security_issue_density=bandit_metrics.issues_per_kloc,
        total_volume=halstead_metrics.total_volume,
        average_volume=halstead_metrics.average_volume,
        average_difficulty=halstead_metrics.average_difficulty,
        average_effort=halstead_metrics.average_effort,
    )
