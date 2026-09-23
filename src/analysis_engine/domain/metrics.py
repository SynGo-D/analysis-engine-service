from pydantic import BaseModel, Field


class ComplexityMetrics(BaseModel):
    """
    Cyclomatic complexity, aggregated from ESLint's `complexity` rule
    violations only — this service doesn't run an independent complexity
    engine, it reads the actual measured value back out of each
    violation's message (see metrics/calculator.py). That means
    `average`/`maximum` only reflect functions that *exceeded* the
    threshold, not every function in the codebase; a file with no
    complexity violations reports `violations=0` and both figures `None`,
    which is an honest "nothing over the line", not "unmeasured".
    """

    violations: int = 0
    maximum: int | None = None
    average: float | None = None


class CognitiveComplexityMetrics(BaseModel):
    """
    Cognitive complexity — a distinct metric from cyclomatic complexity
    (readability/nesting cost, not branch count), sourced from
    eslint-plugin-sonarjs's `sonarjs/cognitive-complexity` rule. Same
    violations-only caveat as ComplexityMetrics applies.
    """

    violations: int = 0
    maximum: int | None = None
    average: float | None = None


class SizeMetrics(BaseModel):
    """
    `largest_file_lines` is measured directly (every analyzed file's line
    count, independent of whether `max-lines` fired), so it's always
    populated. `largest_function_lines` can only be recovered from a
    `max-lines-per-function` violation message — ESLint doesn't report a
    function's line count unless the rule fires — so it's `None` when no
    function exceeded the threshold, not 0.
    """

    largest_file_lines: int = 0
    largest_function_lines: int | None = None
    max_lines_violations: int = 0
    max_lines_per_function_violations: int = 0


class UnusedCodeMetrics(BaseModel):
    """
    Dead-code findings — deliberately not described as "memory leaks" or
    similar; this is unused/unreachable *source*, not a runtime memory
    diagnosis.
    """

    unused_variables: int = 0
    unreachable_code: int = 0


class AnalysisMetrics(BaseModel):
    """
    The full set of quality metrics this service computes for one
    analysis run. Every density/average figure here is computed exactly
    once, in `metrics/calculator.py` — nothing downstream (main-backend,
    web-interface) recalculates from `findings` itself.
    """

    files_analyzed: int = 0
    loc: int = 0

    errors: int = 0
    warnings: int = 0
    total_issues: int = 0

    # Issues per 1,000 lines of code ("issues / KLOC"). 0.0 when loc is 0
    # rather than a division error — a workspace with no JS/TS to analyze
    # has nothing to report a rate for.
    error_density: float = 0.0
    warning_density: float = 0.0
    issue_density: float = 0.0

    complexity: ComplexityMetrics = Field(default_factory=ComplexityMetrics)
    cognitive_complexity: CognitiveComplexityMetrics = Field(default_factory=CognitiveComplexityMetrics)
    size: SizeMetrics = Field(default_factory=SizeMetrics)
    unused_code: UnusedCodeMetrics = Field(default_factory=UnusedCodeMetrics)


class RuleStatistic(BaseModel):
    """Per-rule finding counts — one entry per distinct ESLint rule ID that fired."""

    rule_id: str
    count: int
    errors: int
    warnings: int


class FileStatistic(BaseModel):
    """
    Per-file counts for every analyzed JS/TS file, including files with
    zero findings — `loc` comes from the same direct line count as
    `SizeMetrics.largest_file_lines`, not from ESLint's output.
    """

    file_path: str
    loc: int
    errors: int
    warnings: int
    issues: int
