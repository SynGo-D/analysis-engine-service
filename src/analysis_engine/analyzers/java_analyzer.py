import json
from pathlib import Path

from .analyzer import Analyzer
from .process_runner import ToolExecutionError, run_process
from ..config import settings
from ..domain import AnalysisJob, Finding, FindingCategory
from ..workspace import Workspace

# Rules whose category is decided here rather than inferred, for the same
# reason the ESLint analyzer keeps its own map: the four metric rules are
# load-bearing (metrics/calculator.py derives the dashboard's Complexity,
# Code Size and Unused Code panels from them), and the bug-shaped rules
# are worth separating from general tidiness so severity means something.
#
# Anything in the ruleset but absent here falls back to _DEFAULT_CATEGORY.
_RULE_CATEGORY_MAP: dict[str, FindingCategory] = {
    "CyclomaticComplexity": "complexity",
    "NPathComplexity": "complexity",
    "CognitiveComplexity": "cognitive_complexity",
    "NcssCount": "maintainability",
    "UnusedLocalVariable": "unused_code",
    "UnusedPrivateField": "unused_code",
    "UnusedPrivateMethod": "unused_code",
    "UnusedFormalParameter": "unused_code",
    "UnusedAssignment": "unused_code",
    "BrokenNullCheck": "bug",
    "MisplacedNullCheck": "bug",
    "CompareObjectsWithEquals": "bug",
    "UseEqualsToCompareStrings": "bug",
    "EqualsNull": "bug",
    "OverrideBothEqualsAndHashcode": "bug",
    "ComparisonWithNaN": "bug",
    "CollectionTypeMismatch": "bug",
    "ClassCastExceptionWithToArray": "bug",
    "UnconditionalIfStatement": "bug",
    "IdenticalConditionalBranches": "bug",
    "ImplicitSwitchFallThrough": "bug",
    "AvoidBranchingStatementAsLastInLoop": "bug",
    "JumbledIncrementer": "bug",
    "ReturnFromFinallyBlock": "bug",
    "DoNotThrowExceptionInFinally": "bug",
    "CloseResource": "bug",
    "CheckSkipResult": "bug",
    "ConfusingArgumentToVarargsMethod": "bug",
    "StringBufferInstantiationWithChar": "bug",
    "AvoidDecimalLiteralsInBigDecimalConstructor": "bug",
    "DontUseFloatTypeForLoopIndices": "bug",
    "InstantiationToGetClass": "bug",
    "SuspiciousEqualsMethodName": "bug",
    "ProperCloneImplementation": "bug",
    "DoNotTerminateVM": "bug",
    "AvoidCallingFinalize": "bug",
    "EmptyCatchBlock": "bug",
    "PreserveStackTrace": "maintainability",
    "ExcessiveParameterList": "maintainability",
    "AvoidDeeplyNestedIfStmts": "maintainability",
    "ExceptionAsFlowControl": "maintainability",
}
_DEFAULT_CATEGORY: FindingCategory = "code_smell"

# PMD scores every rule 1 (highest) to 5. Its own documentation treats 1
# and 2 as "change is strongly advised"; the rest are suggestions. That
# maps onto the two severities the rest of the platform has.
_ERROR_PRIORITY_CEILING = 2

# PMD exits 4 when it found violations and 0 when it did not — violations
# are the normal outcome here, not a failure, exactly as ESLint's exit 1
# is. Anything else is a genuine tool or configuration problem.
_SUCCESS_EXIT_CODES = (0, 4)


class JavaAnalyzer(Analyzer):
    """
    PMD for Java, via a fixed ruleset this service owns (tools/pmd/) —
    never the analyzed repository's own ruleset, Maven plugins or build
    files. Same reasoning as the ESLint analyzer: running repository-
    controlled build tooling would undermine "never execute untrusted
    repository code directly on the host."

    PMD reads source and never compiles it, which is why it is used here
    and SpotBugs is not — SpotBugs works on bytecode, so it would need a
    full Maven build on every review, with the repository's own plugins
    executing on this host.

    Parses PMD's `--format json` output directly, so the rule, priority
    and location PMD reports are used as-is.
    """

    @property
    def tool_name(self) -> str:
        return "pmd"

    @property
    def supported_languages(self) -> frozenset[str]:
        return frozenset({"java"})

    @property
    def supported_categories(self) -> frozenset[FindingCategory]:
        return frozenset(_RULE_CATEGORY_MAP.values()) | {_DEFAULT_CATEGORY}

    def build_command(self, workspace: Workspace) -> list[str]:
        return [
            settings.pmd_bin_path,
            "check",
            "--dir", str(workspace.path),
            "--rulesets", settings.pmd_ruleset_path,
            "--format", "json",
            # Without this PMD writes a progress bar to stdout, which is
            # also where the JSON goes.
            "--no-progress",
            # PMD exits 5 on a recoverable error unless told otherwise;
            # a single unparsable file should not fail the whole review,
            # and processingErrors in the output records what happened.
            "--no-fail-on-error",
        ]

    async def analyze(self, workspace: Workspace, job: AnalysisJob) -> list[Finding]:
        result = await run_process(
            self.build_command(workspace),
            cwd=workspace.path,
            timeout=settings.java_analyzer_timeout_seconds,
        )

        if result.returncode not in _SUCCESS_EXIT_CODES:
            raise ToolExecutionError(
                f"pmd exited with unexpected code {result.returncode}: {result.stderr.strip()[:500]}"
            )

        if not result.stdout.strip():
            return []

        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ToolExecutionError(f"pmd produced output that is not JSON: {error}") from error

        findings: list[Finding] = []
        for file_report in report.get("files", []):
            file_path = self._relative_path(file_report["filename"], workspace)
            for violation in file_report.get("violations", []):
                findings.append(self._to_finding(violation, file_path, job))

        return findings

    def _relative_path(self, absolute_path: str, workspace: Workspace) -> str:
        """
        PMD reports absolute paths; everything downstream — Finding.file_path,
        the LOC map, main-backend, web-interface — joins on paths relative
        to the repository root.
        """
        try:
            return Path(absolute_path).relative_to(workspace.path).as_posix()
        except ValueError:
            return absolute_path

    def _to_finding(self, violation: dict, file_path: str, job: AnalysisJob) -> Finding:
        rule_id = violation.get("rule") or "unknown-rule"
        priority = violation.get("priority", 5)

        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=file_path,
            line=violation.get("beginline"),
            column=violation.get("begincolumn"),
            severity="error" if priority <= _ERROR_PRIORITY_CEILING else "warning",
            category=_RULE_CATEGORY_MAP.get(rule_id, _DEFAULT_CATEGORY),
            rule_id=rule_id,
            message=violation.get("description", "").strip(),
            tool=self.tool_name,
            # Computed by FindingNormalizer, as for every other analyzer.
            fingerprint="",
        )
