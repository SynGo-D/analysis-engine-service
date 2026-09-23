from datetime import datetime, timezone
from uuid import uuid4

from analysis_engine.domain import Finding
from analysis_engine.metrics import calculate_file_statistics, calculate_metrics, calculate_rule_statistics


def _finding(**overrides) -> Finding:
    defaults = dict(
        repository="owner/repo",
        pull_request_number=1,
        commit_sha="a" * 40,
        file_path="src/index.ts",
        line=1,
        column=1,
        severity="error",
        category="code_smell",
        rule_id="no-unused-vars",
        message="'x' is assigned a value but never used.",
        tool="eslint",
        fingerprint=str(uuid4()),
    )
    defaults.update(overrides)
    return Finding(**defaults)


def test_calculate_metrics_counts_and_densities():
    findings = [
        _finding(severity="error", rule_id="no-unused-vars"),
        _finding(severity="warning", rule_id="no-console"),
    ]
    file_lines = {"src/index.ts": 500, "src/utils.ts": 500}

    metrics = calculate_metrics(findings, file_lines)

    assert metrics.files_analyzed == 2
    assert metrics.loc == 1000
    assert metrics.errors == 1
    assert metrics.warnings == 1
    assert metrics.total_issues == 2
    # 1 error / 1 KLOC = 1.0
    assert metrics.error_density == 1.0
    assert metrics.warning_density == 1.0
    assert metrics.issue_density == 2.0


def test_calculate_metrics_zero_loc_gives_zero_density_not_a_crash():
    metrics = calculate_metrics([], {})
    assert metrics.loc == 0
    assert metrics.error_density == 0.0
    assert metrics.issue_density == 0.0


def test_calculate_metrics_parses_complexity_value_from_message():
    findings = [
        _finding(
            rule_id="complexity",
            category="complexity",
            message="Function 'handle' has a complexity of 14. Maximum allowed is 10.",
        ),
        _finding(
            rule_id="complexity",
            category="complexity",
            message="Function 'other' has a complexity of 8. Maximum allowed is 10.",
        ),
    ]

    metrics = calculate_metrics(findings, {"src/index.ts": 10})

    assert metrics.complexity.violations == 2
    assert metrics.complexity.maximum == 14
    assert metrics.complexity.average == 11.0


def test_calculate_metrics_cognitive_complexity_is_separate_from_cyclomatic():
    findings = [
        _finding(
            rule_id="complexity",
            category="complexity",
            message="Function 'a' has a complexity of 12. Maximum allowed is 10.",
        ),
        _finding(
            rule_id="sonarjs/cognitive-complexity",
            category="cognitive_complexity",
            message="Refactor this function to reduce its Cognitive Complexity from 21 to the 15 allowed.",
        ),
    ]

    metrics = calculate_metrics(findings, {"src/index.ts": 10})

    assert metrics.complexity.violations == 1
    assert metrics.complexity.maximum == 12
    assert metrics.cognitive_complexity.violations == 1
    assert metrics.cognitive_complexity.maximum == 21


def test_calculate_metrics_size_and_unused_code():
    findings = [
        _finding(
            rule_id="max-lines-per-function",
            category="maintainability",
            message="Function 'big' has too many lines (150). Maximum allowed is 100.",
        ),
        _finding(rule_id="max-lines", category="maintainability", message="File has too many lines (600)."),
        _finding(rule_id="no-unused-vars", category="unused_code"),
        _finding(rule_id="no-unreachable", category="unused_code"),
    ]
    file_lines = {"src/big.ts": 842, "src/small.ts": 10}

    metrics = calculate_metrics(findings, file_lines)

    assert metrics.size.largest_file_lines == 842
    assert metrics.size.largest_function_lines == 150
    assert metrics.size.max_lines_violations == 1
    assert metrics.size.max_lines_per_function_violations == 1
    assert metrics.unused_code.unused_variables == 1
    assert metrics.unused_code.unreachable_code == 1


def test_calculate_rule_statistics_groups_by_rule_most_frequent_first():
    findings = [
        _finding(rule_id="no-unused-vars", severity="error"),
        _finding(rule_id="no-unused-vars", severity="error"),
        _finding(rule_id="no-console", severity="warning"),
    ]

    stats = calculate_rule_statistics(findings)

    assert stats[0].rule_id == "no-unused-vars"
    assert stats[0].count == 2
    assert stats[0].errors == 2
    assert stats[0].warnings == 0
    assert stats[1].rule_id == "no-console"
    assert stats[1].count == 1
    assert stats[1].warnings == 1


def test_calculate_file_statistics_includes_clean_files_with_zero_findings():
    findings = [_finding(file_path="src/dirty.ts", severity="error")]
    file_lines = {"src/dirty.ts": 100, "src/clean.ts": 50}

    stats = calculate_file_statistics(findings, file_lines)

    by_path = {s.file_path: s for s in stats}
    assert by_path["src/dirty.ts"].errors == 1
    assert by_path["src/dirty.ts"].issues == 1
    assert by_path["src/clean.ts"].errors == 0
    assert by_path["src/clean.ts"].issues == 0
    assert by_path["src/clean.ts"].loc == 50
