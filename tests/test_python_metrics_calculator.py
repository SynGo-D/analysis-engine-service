from uuid import uuid4

from analysis_engine.domain import Finding, RadonComplexityEntry, RadonMaintainabilityEntry, RadonRawLocEntry
from analysis_engine.metrics.python.calculator import (
    calculate_bandit_metrics,
    calculate_maintainability_metrics,
    calculate_pylint_metrics,
    calculate_python_loc,
    calculate_radon_complexity_metrics,
)


def _finding(**overrides) -> Finding:
    defaults = dict(
        repository="owner/repo", pull_request_number=1, commit_sha="a" * 40,
        file_path="src/a.py", severity="warning", category="code_smell",
        rule_id="some-rule", message="msg", tool="pylint", fingerprint=str(uuid4()),
    )
    defaults.update(overrides)
    return Finding(**defaults)


# ---------------------------------------------------------------------------
# LOC / KLOC
# ---------------------------------------------------------------------------


def test_calculate_python_loc_aggregates_across_files():
    entries = [
        RadonRawLocEntry(file="a.py", loc=500, lloc=400, sloc=380, comments=20, multi=10, blank=90, single_comments=5),
        RadonRawLocEntry(file="b.py", loc=500, lloc=400, sloc=380, comments=20, multi=10, blank=90, single_comments=5),
    ]

    loc = calculate_python_loc(entries)

    assert loc.physical_loc == 1000
    assert loc.logical_loc == 800
    assert loc.comment_loc == 70  # (20+10+5) * 2
    assert loc.blank_loc == 180
    assert loc.kloc == 1.0


def test_calculate_python_loc_handles_empty_input():
    loc = calculate_python_loc([])
    assert loc.physical_loc == 0
    assert loc.kloc == 0.0


# ---------------------------------------------------------------------------
# Pylint
# ---------------------------------------------------------------------------


def test_calculate_pylint_metrics_counts_by_type_and_groups():
    findings = [
        _finding(rule_id="no-unused-vars", category="code_smell", metadata={"pylintType": "warning"}),
        _finding(rule_id="missing-docstring", category="style", metadata={"pylintType": "convention"}),
        _finding(rule_id="missing-docstring", category="style", metadata={"pylintType": "convention"}, file_path="src/b.py"),
    ]

    metrics = calculate_pylint_metrics(findings, kloc=1.0)

    assert metrics.total_issues == 3
    assert metrics.warnings == 1
    assert metrics.conventions == 2
    assert metrics.issues_per_kloc == 3.0
    assert metrics.issues_by_rule == {"no-unused-vars": 1, "missing-docstring": 2}
    assert metrics.issues_by_file == {"src/a.py": 2, "src/b.py": 1}
    assert metrics.issues_by_category == {"code_smell": 1, "style": 2}


def test_calculate_pylint_metrics_zero_kloc_gives_zero_density_not_a_crash():
    metrics = calculate_pylint_metrics([_finding()], kloc=0.0)
    assert metrics.issues_per_kloc == 0.0


def test_calculate_pylint_metrics_empty_input():
    metrics = calculate_pylint_metrics([], kloc=2.0)
    assert metrics.total_issues == 0
    assert metrics.issues_by_rule == {}


# ---------------------------------------------------------------------------
# Radon complexity / maintainability
# ---------------------------------------------------------------------------


def test_calculate_radon_complexity_metrics():
    entries = [
        RadonComplexityEntry(file="a.py", object="f1", type="function", line=1, end_line=5, complexity=15, rank="C"),
        RadonComplexityEntry(file="a.py", object="f2", type="function", line=10, end_line=12, complexity=3, rank="A"),
    ]

    metrics = calculate_radon_complexity_metrics(entries, kloc=2.0)

    assert metrics.total_complexity == 18
    assert metrics.average_complexity == 9.0
    assert metrics.maximum_complexity == 15
    assert metrics.high_complexity_function_count == 1
    assert metrics.complexity_distribution == {"C": 1, "A": 1}
    assert metrics.complexity_per_kloc == 9.0


def test_calculate_radon_complexity_metrics_empty_input_gives_none_not_zero():
    metrics = calculate_radon_complexity_metrics([])
    assert metrics.average_complexity is None
    assert metrics.maximum_complexity is None
    assert metrics.high_complexity_function_count == 0


def test_calculate_maintainability_metrics():
    entries = [
        RadonMaintainabilityEntry(file="a.py", mi=80.0, rank="A"),
        RadonMaintainabilityEntry(file="b.py", mi=20.0, rank="A"),
    ]

    metrics = calculate_maintainability_metrics(entries)

    assert metrics.average_maintainability_index == 50.0
    assert metrics.minimum_maintainability_index == 20.0
    assert metrics.maximum_maintainability_index == 80.0
    assert metrics.low_maintainability_file_count == 1  # only the 20.0 file is below the 65 threshold


# ---------------------------------------------------------------------------
# Bandit
# ---------------------------------------------------------------------------


def test_calculate_bandit_metrics_counts_by_severity_and_confidence():
    findings = [
        _finding(tool="bandit", category="vulnerability", severity="error", rule_id="B602", metadata={"confidence": "HIGH"}),
        _finding(tool="bandit", category="vulnerability", severity="warning", rule_id="B608", metadata={"confidence": "MEDIUM"}),
        _finding(tool="bandit", category="vulnerability", severity="info", rule_id="B105", metadata={"confidence": "LOW"}),
    ]

    metrics = calculate_bandit_metrics(findings, kloc=1.0)

    assert metrics.total_issues == 3
    assert metrics.high_severity == 1
    assert metrics.medium_severity == 1
    assert metrics.low_severity == 1
    assert metrics.high_confidence == 1
    assert metrics.issues_per_kloc == 3.0


def test_calculate_bandit_metrics_excludes_parse_error_findings():
    findings = [
        _finding(tool="bandit", category="vulnerability", severity="error", rule_id="B602"),
        _finding(tool="bandit", category="bug", severity="error", rule_id="bandit-parse-error"),
    ]

    metrics = calculate_bandit_metrics(findings, kloc=1.0)

    assert metrics.total_issues == 1  # the parse-error finding isn't a security issue
