import xml.etree.ElementTree as ET

from .analyzer import Analyzer
from .process_runner import ToolExecutionError, run_process
from ..config import settings
from ..domain import AnalysisJob, Finding, FindingCategory, Severity
from ..workspace import Workspace

_SEVERITY_MAP: dict[str, Severity] = {
    "error": "error",
    "warning": "warning",
}  # anything else (style/performance/portability/information) -> "info", handled in code


class CppcheckAnalyzer(Analyzer):
    """
    Cppcheck for C/C++.

    Confirmed via `reviewdog -list`: no built-in Reviewdog parser exists
    (Phase 5's "checkstyle" guess was wrong — Cppcheck's --xml output is
    its own schema, not checkstyle-compatible). Parsed directly here with
    the stdlib xml.etree, same treatment as Pylint/Radon.

    Non-obvious, confirmed by direct testing rather than assumed: Cppcheck
    writes its --xml report to **stderr**, not stdout (stdout only gets
    progress lines like "Checking file.c ..."). Reading stdout here would
    have silently produced zero findings on every run.
    """

    @property
    def tool_name(self) -> str:
        return "cppcheck"

    @property
    def supported_languages(self) -> frozenset[str]:
        return frozenset({"c", "cpp"})

    @property
    def supported_categories(self) -> frozenset[FindingCategory]:
        return frozenset({"bug", "vulnerability", "code_smell", "style"})

    @property
    def reviewdog_format(self) -> str:
        return "n/a (parsed directly, no Reviewdog built-in parser exists)"

    def build_command(self, workspace: Workspace) -> list[str]:
        # unusedFunction is deliberately excluded: it requires whole-
        # program analysis and is prone to false positives when checking
        # files in isolation without their real callers. "information"
        # level (missingInclude, checkersReport, etc.) is Cppcheck's own
        # environment notes, not code issues, so also excluded.
        return [
            settings.cppcheck_bin_path,
            "--enable=warning,style,performance,portability",
            "--xml",
            ".",
        ]

    async def analyze(self, workspace: Workspace, job: AnalysisJob) -> list[Finding]:
        result = await run_process(
            self.build_command(workspace), cwd=workspace.path, timeout=settings.analyzer_timeout_seconds,
        )

        # Cppcheck returns 0 even when it finds issues (confirmed by
        # testing) — a nonzero exit here means the tool itself failed.
        if result.returncode != 0:
            raise ToolExecutionError(
                f"cppcheck exited with code {result.returncode}: {result.stderr[:500]!r}"
            )

        xml_output = result.stderr  # see class docstring — the report is on stderr, not stdout
        if not xml_output.strip():
            return []

        root = ET.fromstring(xml_output)
        findings: list[Finding] = []

        for error_el in root.findall(".//error"):
            location_el = error_el.find("location")
            if location_el is None:
                # e.g. "checkersReport" — a meta/summary entry with no
                # associated file, not a real per-file issue.
                continue

            findings.append(self._to_finding(error_el, location_el, job))

        return findings

    def _to_finding(self, error_el: ET.Element, location_el: ET.Element, job: AnalysisJob) -> Finding:
        cppcheck_severity = error_el.get("severity", "style")
        rule_id = error_el.get("id", "cppcheck")
        has_cwe = error_el.get("cwe") is not None

        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=location_el.get("file", "<unknown>"),
            line=int(location_el.get("line")) if location_el.get("line") else None,
            column=int(location_el.get("column")) if location_el.get("column") else None,
            severity=_SEVERITY_MAP.get(cppcheck_severity, "info"),
            # Same caveat as the other analyzers — coarse, not per-rule;
            # a CWE ID present is treated as a reasonable "vulnerability"
            # signal, refined further in Phase 7.
            category=self._category_for(cppcheck_severity, has_cwe),
            rule_id=rule_id,
            message=error_el.get("msg", ""),
            tool=self.tool_name,
            fingerprint="",  # Phase 7 — see EslintAnalyzer's comment for why this is deliberately deferred
        )

    def _category_for(self, cppcheck_severity: str, has_cwe: bool) -> FindingCategory:
        if has_cwe:
            return "vulnerability"
        if cppcheck_severity in ("error", "warning"):
            return "bug"
        if cppcheck_severity == "style":
            return "style"
        return "code_smell"  # performance, portability
