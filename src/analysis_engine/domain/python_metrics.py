from pydantic import BaseModel, Field

from .analyzer_status import AnalyzerRunStatus
from .finding import Finding

# ---------------------------------------------------------------------------
# Raw, tool-specific shapes — never discarded, even though the aggregate
# PythonMetrics below (built from these by metrics/python/calculator.py) is
# what most consumers actually display. Field names mirror each tool's own
# JSON output field names (see analyzers/python/README.md), not renamed to a
# house style, so this stays recognizable against the tool's own docs.
# ---------------------------------------------------------------------------


class RadonComplexityEntry(BaseModel):
    """One function/method/class from `radon cc --json`."""

    file: str
    object: str
    type: str
    line: int
    end_line: int
    complexity: int
    rank: str


class RadonMaintainabilityEntry(BaseModel):
    """One file's maintainability index from `radon mi --json`."""

    file: str
    mi: float
    rank: str


class HalsteadEntry(BaseModel):
    h1: int
    h2: int
    N1: int
    N2: int
    vocabulary: int
    length: int
    calculated_length: float
    volume: float
    difficulty: float
    effort: float
    time: float
    bugs: float


class RadonHalsteadEntry(BaseModel):
    """One file's Halstead metrics from `radon hal --json` — file-level total plus per-function breakdown."""

    file: str
    total: HalsteadEntry
    functions: dict[str, HalsteadEntry] = Field(default_factory=dict)


class RadonRawLocEntry(BaseModel):
    """One file's LOC breakdown from `radon raw --json` — the single source of truth for Python LOC (see metrics/python/README.md)."""

    file: str
    loc: int
    lloc: int
    sloc: int
    comments: int
    multi: int
    blank: int
    single_comments: int


# ---------------------------------------------------------------------------
# Aggregates — computed once, by metrics/python/calculator.py, from the raw
# shapes above (plus Pylint/Bandit findings). Every consumer only displays
# these; nothing recomputes a density/average itself.
# ---------------------------------------------------------------------------


class PythonLoc(BaseModel):
    physical_loc: int = 0
    logical_loc: int = 0
    source_loc: int = 0
    comment_loc: int = 0
    blank_loc: int = 0
    kloc: float = 0.0


class PylintMetrics(BaseModel):
    total_issues: int = 0
    errors: int = 0
    warnings: int = 0
    conventions: int = 0
    refactors: int = 0
    fatals: int = 0
    issues_per_kloc: float = 0.0
    issues_by_file: dict[str, int] = Field(default_factory=dict)
    issues_by_category: dict[str, int] = Field(default_factory=dict)
    issues_by_rule: dict[str, int] = Field(default_factory=dict)


class RadonComplexityMetrics(BaseModel):
    total_complexity: int = 0
    average_complexity: float | None = None
    maximum_complexity: int | None = None
    # Rank letter ("A".."F") -> number of functions/methods/classes at that rank.
    complexity_distribution: dict[str, int] = Field(default_factory=dict)
    high_complexity_function_count: int = 0
    complexity_per_kloc: float = 0.0


class MaintainabilityMetrics(BaseModel):
    average_maintainability_index: float | None = None
    minimum_maintainability_index: float | None = None
    maximum_maintainability_index: float | None = None
    low_maintainability_file_count: int = 0


class HalsteadMetrics(BaseModel):
    total_volume: float = 0.0
    average_volume: float | None = None
    average_difficulty: float | None = None
    average_effort: float | None = None


class BanditMetrics(BaseModel):
    total_issues: int = 0
    high_severity: int = 0
    medium_severity: int = 0
    low_severity: int = 0
    high_confidence: int = 0
    issues_per_kloc: float = 0.0
    issues_by_rule: dict[str, int] = Field(default_factory=dict)
    issues_by_file: dict[str, int] = Field(default_factory=dict)


class PythonMetrics(BaseModel):
    """The combined, cross-tool Python aggregate — section 7's "Python-level abstract metrics"."""

    loc: PythonLoc = Field(default_factory=PythonLoc)

    pylint_issue_count: int = 0
    pylint_issue_density: float = 0.0

    average_cyclomatic_complexity: float | None = None
    maximum_cyclomatic_complexity: int | None = None
    high_complexity_function_count: int = 0

    average_maintainability_index: float | None = None
    minimum_maintainability_index: float | None = None
    low_maintainability_file_count: int = 0

    bandit_issue_count: int = 0
    high_severity_security_issue_count: int = 0
    medium_severity_security_issue_count: int = 0
    low_severity_security_issue_count: int = 0
    security_issue_density: float = 0.0

    total_volume: float = 0.0
    average_volume: float | None = None
    average_difficulty: float | None = None
    average_effort: float | None = None


# ---------------------------------------------------------------------------
# Per-tool run results — status + findings + that tool's own raw output +
# that tool's own metrics, exactly the "analyzers": {pylint: {...}, ...}
# shape the result contract calls for.
# ---------------------------------------------------------------------------


class PylintRun(BaseModel):
    status: AnalyzerRunStatus
    error_message: str | None = None
    duration_seconds: float = 0.0
    findings: list[Finding] = Field(default_factory=list)
    metrics: PylintMetrics = Field(default_factory=PylintMetrics)


class RadonRun(BaseModel):
    status: AnalyzerRunStatus
    error_message: str | None = None
    duration_seconds: float = 0.0
    findings: list[Finding] = Field(default_factory=list)
    complexity: list[RadonComplexityEntry] = Field(default_factory=list)
    maintainability: list[RadonMaintainabilityEntry] = Field(default_factory=list)
    halstead: list[RadonHalsteadEntry] = Field(default_factory=list)
    raw_loc: list[RadonRawLocEntry] = Field(default_factory=list)
    complexity_metrics: RadonComplexityMetrics = Field(default_factory=RadonComplexityMetrics)
    maintainability_metrics: MaintainabilityMetrics = Field(default_factory=MaintainabilityMetrics)
    halstead_metrics: HalsteadMetrics = Field(default_factory=HalsteadMetrics)


class BanditRun(BaseModel):
    status: AnalyzerRunStatus
    error_message: str | None = None
    duration_seconds: float = 0.0
    findings: list[Finding] = Field(default_factory=list)
    metrics: BanditMetrics = Field(default_factory=BanditMetrics)


class PythonAnalysisResult(BaseModel):
    """
    Everything the Python analyzer produced for one job — attached at
    `AnalysisResult.python` (see domain/analysis_result.py). `None` on
    AnalysisResult when no Python was detected in the workspace at all,
    distinct from "Python was detected but every tool failed" (which is
    still a populated PythonAnalysisResult, just with every sub-run at
    EXECUTION_ERROR/TIMEOUT and empty findings).
    """

    pylint: PylintRun
    radon: RadonRun
    bandit: BanditRun
    metrics: PythonMetrics = Field(default_factory=PythonMetrics)
