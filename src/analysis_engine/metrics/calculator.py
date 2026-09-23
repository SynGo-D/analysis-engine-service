import re
from collections import defaultdict

from ..domain import (
    AnalysisMetrics,
    ComplexityMetrics,
    CognitiveComplexityMetrics,
    SizeMetrics,
    UnusedCodeMetrics,
    FileStatistic,
    Finding,
    RuleStatistic,
)

# Each of these parses the actual measured value back out of the rule
# violation that reported it — see tools/eslint/eslint.config.cjs and
# tools/pmd/ruleset.xml for the exact wording each tool produces. This is
# the only place in the service that does this parsing; nothing
# downstream repeats it.
#
# One pattern per tool per metric rather than one clever combined regex:
# the wordings have nothing in common, and a single pattern that matched
# both would match neither clearly.
_COMPLEXITY_VALUE = re.compile(
    r"has a complexity of (\d+)"                 # eslint: complexity
    r"|has a cyclomatic complexity of (\d+)"     # pmd: CyclomaticComplexity
)
_COGNITIVE_COMPLEXITY_VALUE = re.compile(
    r"Cognitive Complexity from (\d+) to"        # eslint: sonarjs
    r"|has a cognitive complexity of (\d+)"      # pmd: CognitiveComplexity
)
_FUNCTION_LINES_VALUE = re.compile(
    r"has too many lines \((\d+)\)"             # eslint: max-lines-per-function
    r"|method '[^']*' has a NCSS line count of (\d+)"   # pmd: NcssCount, method
)

# PMD reports class and method sizes through the same NcssCount rule,
# distinguished only by how the message starts, so the two are separated
# here rather than by rule id.
_CLASS_LINES_VALUE = re.compile(r"class '[^']*' has a NCSS line count of (\d+)")

_COMPLEXITY_RULES = {"complexity", "CyclomaticComplexity"}
_COGNITIVE_COMPLEXITY_RULES = {"sonarjs/cognitive-complexity", "CognitiveComplexity"}
_FUNCTION_LINES_RULES = {"max-lines-per-function", "NcssCount"}

_UNUSED_VARIABLE_RULES = {
    "no-unused-vars", "@typescript-eslint/no-unused-vars",          # eslint
    "UnusedLocalVariable", "UnusedPrivateField", "UnusedAssignment",  # pmd
    "UnusedPrivateMethod", "UnusedFormalParameter",
}

# Java's compiler rejects unreachable statements outright, so PMD has no
# equivalent of no-unreachable and this count is structurally zero for
# Java — absent by construction, not by omission.
_UNREACHABLE_CODE_RULES = {"no-unreachable"}


def _measured(pattern: re.Pattern[str], message: str) -> int | None:
    """
    The first numeric group that matched. The alternations above put each
    tool's wording in its own group, so exactly one is ever populated.
    """
    match = pattern.search(message)
    if match is None:
        return None

    value = next((group for group in match.groups() if group is not None), None)
    return int(value) if value is not None else None


def calculate_metrics(findings: list[Finding], file_lines: dict[str, int]) -> AnalysisMetrics:
    """
    The single place AnalysisMetrics gets computed — from `findings`
    (already normalized/deduplicated by FindingNormalizer) and each
    analyzed file's independently-measured line count. Every consumer of
    AnalysisResult (main-backend, web-interface) only ever displays these
    values.
    """
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    total_issues = errors + warnings

    loc = sum(file_lines.values())
    kloc = loc / 1000 if loc else 0.0

    complexity_values = [
        value
        for f in findings if f.rule_id in _COMPLEXITY_RULES
        if (value := _measured(_COMPLEXITY_VALUE, f.message)) is not None
    ]
    cognitive_values = [
        value
        for f in findings if f.rule_id in _COGNITIVE_COMPLEXITY_RULES
        if (value := _measured(_COGNITIVE_COMPLEXITY_VALUE, f.message)) is not None
    ]
    function_line_values = [
        value
        for f in findings if f.rule_id in _FUNCTION_LINES_RULES
        if (value := _measured(_FUNCTION_LINES_VALUE, f.message)) is not None
    ]
    # PMD reports an oversized class through NcssCount as well, so a
    # file-size violation is either ESLint's max-lines or the class-shaped
    # half of NcssCount.
    file_line_violations = sum(
        1 for f in findings
        if f.rule_id == "max-lines"
        or (f.rule_id == "NcssCount" and _CLASS_LINES_VALUE.search(f.message))
    )

    return AnalysisMetrics(
        files_analyzed=len(file_lines),
        loc=loc,
        errors=errors,
        warnings=warnings,
        total_issues=total_issues,
        error_density=round(errors / kloc, 2) if kloc else 0.0,
        warning_density=round(warnings / kloc, 2) if kloc else 0.0,
        issue_density=round(total_issues / kloc, 2) if kloc else 0.0,
        complexity=ComplexityMetrics(
            violations=len(complexity_values),
            maximum=max(complexity_values) if complexity_values else None,
            average=round(sum(complexity_values) / len(complexity_values), 1) if complexity_values else None,
        ),
        cognitive_complexity=CognitiveComplexityMetrics(
            violations=len(cognitive_values),
            maximum=max(cognitive_values) if cognitive_values else None,
            average=round(sum(cognitive_values) / len(cognitive_values), 1) if cognitive_values else None,
        ),
        size=SizeMetrics(
            largest_file_lines=max(file_lines.values()) if file_lines else 0,
            largest_function_lines=max(function_line_values) if function_line_values else None,
            max_lines_violations=file_line_violations,
            max_lines_per_function_violations=len(function_line_values),
        ),
        unused_code=UnusedCodeMetrics(
            unused_variables=sum(1 for f in findings if f.rule_id in _UNUSED_VARIABLE_RULES),
            unreachable_code=sum(1 for f in findings if f.rule_id in _UNREACHABLE_CODE_RULES),
        ),
    )


def calculate_rule_statistics(findings: list[Finding]) -> list[RuleStatistic]:
    """One entry per distinct rule ID that fired, most-frequent first (a default order — sorting itself is a frontend concern)."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "errors": 0, "warnings": 0})

    for finding in findings:
        bucket = counts[finding.rule_id]
        bucket["count"] += 1
        bucket["errors" if finding.severity == "error" else "warnings"] += 1

    statistics = [
        RuleStatistic(rule_id=rule_id, count=v["count"], errors=v["errors"], warnings=v["warnings"])
        for rule_id, v in counts.items()
    ]
    return sorted(statistics, key=lambda s: s.count, reverse=True)


def calculate_file_statistics(findings: list[Finding], file_lines: dict[str, int]) -> list[FileStatistic]:
    """
    One entry per analyzed file (including files with zero findings, so a
    clean file still shows up with its LOC), sorted by file path (a
    default order — sorting/filtering itself is a frontend concern).
    """
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"errors": 0, "warnings": 0})

    for finding in findings:
        bucket = counts[finding.file_path]
        bucket["errors" if finding.severity == "error" else "warnings"] += 1

    statistics = [
        FileStatistic(
            file_path=file_path,
            loc=loc,
            errors=counts[file_path]["errors"],
            warnings=counts[file_path]["warnings"],
            issues=counts[file_path]["errors"] + counts[file_path]["warnings"],
        )
        for file_path, loc in file_lines.items()
    ]
    return sorted(statistics, key=lambda s: s.file_path)
