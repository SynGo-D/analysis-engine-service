from enum import Enum


class AnalyzerRunStatus(str, Enum):
    """
    Distinguishes "the tool ran and found nothing" from "the tool ran and
    found issues" from "the tool itself failed to produce a result" —
    collapsing these into a single success/failure boolean is exactly
    what makes a nonzero exit code (which Pylint and Bandit both use to
    mean "issues found", not "crashed") get misread as an execution
    failure. See analyzers/python/README.md for the exact exit-code
    semantics each tool uses that this maps from.
    """

    SUCCESS = "success"
    COMPLETED_WITH_FINDINGS = "completed_with_findings"
    EXECUTION_ERROR = "execution_error"
    TIMEOUT = "timeout"
