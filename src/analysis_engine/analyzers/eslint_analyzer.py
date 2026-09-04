import json
from pathlib import Path

from .analyzer import Analyzer
from .process_runner import ToolExecutionError, run_process
from ..config import settings
from ..domain import AnalysisJob, Finding, FindingCategory
from ..workspace import Workspace

# Rules whose category is well understood ahead of time. Everything else
# falls back to _DEFAULT_CATEGORY — eslint:recommended and
# typescript-eslint/recommended cover well over a hundred rules between
# them, and hand-mapping every one is its own substantial task; this list
# covers the rules AnalysisMetrics is actually derived from (so their
# category is load-bearing, not cosmetic) plus the handful of clearly
# bug-shaped built-ins worth calling out explicitly.
_RULE_CATEGORY_MAP: dict[str, FindingCategory] = {
    "complexity": "complexity",
    "sonarjs/cognitive-complexity": "cognitive_complexity",
    "max-lines": "maintainability",
    "max-lines-per-function": "maintainability",
    "no-unused-vars": "unused_code",
    "@typescript-eslint/no-unused-vars": "unused_code",
    "no-unreachable": "unused_code",
    "no-undef": "bug",
    "no-dupe-args": "bug",
    "no-dupe-keys": "bug",
    "no-duplicate-case": "bug",
    "no-const-assign": "bug",
    "no-invalid-regexp": "bug",
    "no-obj-calls": "bug",
    "use-isnan": "bug",
    "valid-typeof": "bug",
}
_DEFAULT_CATEGORY: FindingCategory = "code_smell"


class EslintAnalyzer(Analyzer):
    """
    ESLint for JavaScript/TypeScript, via a fixed ruleset this service
    owns (tools/eslint/) — never the analyzed repository's own ESLint
    config or devDependencies. See analyzers/README.md for why: running
    `npm install` against a repo-controlled package.json (postinstall
    scripts can execute arbitrary code) would undermine "never execute
    untrusted repository code directly on the host."

    Parses ESLint's own `--format json` output directly — no external
    aggregation layer sits between ESLint and Finding construction, so
    the category/severity/location fields ESLint reports are used as-is.
    """

    @property
    def tool_name(self) -> str:
        return "eslint"

    @property
    def supported_languages(self) -> frozenset[str]:
        return frozenset({"javascript", "typescript"})

    @property
    def supported_categories(self) -> frozenset[FindingCategory]:
        return frozenset(_RULE_CATEGORY_MAP.values()) | {_DEFAULT_CATEGORY}

    def build_command(self, workspace: Workspace) -> list[str]:
        return [
            settings.eslint_bin_path,
            "--config", settings.eslint_config_path,
            "--no-config-lookup",
            "--format", "json",
            ".",
        ]

    async def analyze(self, workspace: Workspace, job: AnalysisJob) -> list[Finding]:
        eslint_result = await run_process(
            self.build_command(workspace), cwd=workspace.path, timeout=settings.analyzer_timeout_seconds,
        )

        # ESLint exits 1 when it finds lint problems (confirmed by direct
        # testing) — that's success for us, not a crash. Anything outside
        # {0, 1} is a genuine tool/config failure.
        if eslint_result.returncode not in (0, 1):
            raise ToolExecutionError(
                f"eslint exited with unexpected code {eslint_result.returncode}: "
                f"{eslint_result.stderr.strip()}"
            )

        if not eslint_result.stdout.strip():
            return []

        file_reports = json.loads(eslint_result.stdout)

        findings: list[Finding] = []
        for file_report in file_reports:
            file_path = self._relative_path(file_report["filePath"], workspace)
            for message in file_report.get("messages", []):
                findings.append(self._to_finding(message, file_path, job))

        return findings

    def _relative_path(self, absolute_path: str, workspace: Workspace) -> str:
        """
        ESLint's JSON formatter always reports absolute file paths;
        everything downstream (Finding.file_path, metrics/file_scanner.py's
        LOC map, main-backend, web-interface) works with paths relative to
        the repository root, so the join key matches on both sides.
        """
        try:
            return Path(absolute_path).relative_to(workspace.path).as_posix()
        except ValueError:
            return absolute_path

    def _to_finding(self, message: dict, file_path: str, job: AnalysisJob) -> Finding:
        # A fatal parse error (unparsable syntax) has ruleId: null —
        # still a real finding, just not tied to a specific rule.
        rule_id = message.get("ruleId") or "parse-error"

        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=file_path,
            line=message.get("line"),
            column=message.get("column"),
            severity="error" if message.get("severity") == 2 else "warning",
            category=_RULE_CATEGORY_MAP.get(rule_id, _DEFAULT_CATEGORY),
            rule_id=rule_id,
            message=message["message"],
            tool=self.tool_name,
            # Computed by FindingNormalizer, not here — see its docstring
            # for exactly what does and doesn't go into the hash.
            fingerprint="",
        )
