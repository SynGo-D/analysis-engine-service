import json
import re

from .analyzer import Analyzer
from .process_runner import ToolExecutionError, run_process
from ..config import settings
from ..domain import AnalysisJob, Finding, FindingCategory
from ..workspace import Workspace

# rdjsonl's "message" field for the eslint format keeps the rule ID
# embedded in the text (reviewdog's eslint parser expects ESLint's
# "stylish" output, which right-aligns the rule ID after 2+ spaces —
# confirmed by piping real eslint output through reviewdog directly).
# There's no separate structured field for it, so it's extracted here.
_RULE_ID_PATTERN = re.compile(r"^(.*\S)\s{2,}(\S+)$")


class EslintAnalyzer(Analyzer):
    """
    ESLint for JavaScript/TypeScript, via a fixed ruleset this service
    owns (tools/eslint/) — never the analyzed repository's own ESLint
    config or devDependencies. See analyzers/README.md for why: running
    `npm install` against a repo-controlled package.json (postinstall
    scripts can execute arbitrary code) would undermine "never execute
    untrusted repository code directly on the host."

    This is the one analyzer that genuinely routes through Reviewdog's
    own format parsing — confirmed via `reviewdog -list`, ESLint is the
    only one of the four initial tools with a real built-in Reviewdog
    parser (Pylint/Radon/Cppcheck do not, despite Phase 5's placeholder
    assumption otherwise for two of them).
    """

    @property
    def tool_name(self) -> str:
        return "eslint"

    @property
    def supported_languages(self) -> frozenset[str]:
        return frozenset({"javascript", "typescript"})

    @property
    def supported_categories(self) -> frozenset[FindingCategory]:
        return frozenset({"bug", "code_smell", "style"})

    @property
    def reviewdog_format(self) -> str:
        return "eslint"

    def build_command(self, workspace: Workspace) -> list[str]:
        return [
            settings.eslint_bin_path,
            "--config", settings.eslint_config_path,
            "--no-config-lookup",
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

        reviewdog_result = await run_process(
            [
                settings.reviewdog_bin_path,
                f"-f={self.reviewdog_format}",
                "-reporter=rdjsonl",
                "-filter-mode=nofilter",  # report everything, not just PR-diff-added lines — see analyzers/README.md
                "-level=info",
            ],
            cwd=workspace.path,
            timeout=settings.analyzer_timeout_seconds,
            stdin_data=eslint_result.stdout,
        )

        findings: list[Finding] = []
        for line in reviewdog_result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            diagnostic = json.loads(line)
            findings.append(self._to_finding(diagnostic, job))

        return findings

    def _to_finding(self, diagnostic: dict, job: AnalysisJob) -> Finding:
        raw_message = diagnostic["message"]
        match = _RULE_ID_PATTERN.match(raw_message)
        message, rule_id = (match.group(1), match.group(2)) if match else (raw_message, "eslint")

        location = diagnostic["location"]
        start = location["range"]["start"]

        return Finding(
            repository=job.repository,
            pull_request_number=job.pull_request_number,
            commit_sha=job.commit_sha,
            file_path=location["path"],
            line=start.get("line"),
            column=start.get("column"),
            severity="error" if diagnostic.get("severity") == "ERROR" else "warning",
            # Placeholder, not a considered mapping: Reviewdog's rdjsonl
            # output for eslint doesn't carry a category, and mapping real
            # rule IDs (no-unused-vars -> code_smell, no-undef -> bug,
            # etc.) to FindingCategory deserves more thought than a rushed
            # inline decision while also standing up three other tools'
            # integrations — genuinely Phase 7's job, not skipped here.
            category="bug",
            rule_id=rule_id,
            message=message,
            tool=self.tool_name,
            # Placeholder, not computed: what should and shouldn't be part
            # of a stable fingerprint (e.g. should line number count, given
            # code shifting up/down shouldn't register as a "new" issue?)
            # is a real design question Phase 7 owns — an empty string
            # here is an honest "not yet", not a rushed guess.
            fingerprint="",
        )
