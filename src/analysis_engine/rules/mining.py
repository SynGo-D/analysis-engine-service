import asyncio
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..agents.reviewer.validation import FileCache, check_evidence
from ..agents.rule_miner import RuleMinerOutput, run_rule_miner
from ..agents.runtime.provider import LLMProvider
from ..config import settings
from ..diffing import is_generated
from ..domain import AnalysisJob
from ..domain.business_rule import BusinessRule, glob_match
from ..domain.code_index import RepoIndex
from ..indexing import CodeIndexer
from ..retrieval import RetrievalTools, ToolExecutor, is_secret_file, is_test_file, redact
from ..workspace import WorkspaceManager, run_git_output, validate_branch, validate_clone_url
from .rule_set import RULES_FILES, parse_rules_file

logger = logging.getLogger(__name__)

_CLONE_HOSTS = {"github": "https://github.com", "gitlab": "https://gitlab.com"}
_MAX_PATHS = 300
_README_CHARS = 6_000
_DOC_CHARS = 1_500
_MAX_DOCS = 4
_MAX_TEST_NAMES = 150
_MAX_CONSTRAINT_LINES = 80
# Where constraints get written down in code: docstrings, comments, error
# messages. A literal-word regex of our own, never user input.
_CONSTRAINT_WORDS = r"\b(must|must not|never|only|cannot|can't|not allowed|forbidden|require[sd]?|at most|at least|limit)\b"


@dataclass
class MiningResult:
    suggestions: list[BusinessRule] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    stop_reason: str = ""
    cost_usd: float | None = 0.0
    error: str | None = None


class RuleMiner:
    """
    Suggests business rules for a repository from what it already says
    about itself (docs/agent-architecture.md §8.3). Output is *suggestions*:
    stored with status "suggested" and never used in a review until a
    person accepts them, because a wrong rule would be applied to every
    future PR with full confidence.
    """

    def __init__(self, provider: LLMProvider, workspace_manager: WorkspaceManager | None = None):
        self._provider = provider
        self._workspaces = workspace_manager or WorkspaceManager()

    async def mine_remote(self, provider_name: str, repository: str, branch: str,
                          existing: list[BusinessRule]) -> MiningResult:
        """Clones `repository` at the tip of `branch` and mines it."""
        host = _CLONE_HOSTS.get(provider_name)
        if host is None:
            return MiningResult(error=f"unknown provider {provider_name!r}")
        clone_url = f"{host}/{repository}.git"
        validate_clone_url(clone_url)
        validate_branch(branch)

        head = await _branch_head(clone_url, branch, await self._workspaces.git_env_for(provider_name, repository))
        if head is None:
            return MiningResult(error=f"branch {branch!r} not found in {repository}")

        job = AnalysisJob(provider=provider_name, repository=repository, clone_url=clone_url, commit_sha=head,
                          branch=branch, pull_request_number=0, queued_at="rule-mining")
        async with self._workspaces.prepare(job) as workspace:
            return await self.mine(workspace.path, existing)

    async def mine(self, workspace: Path, existing: list[BusinessRule]) -> MiningResult:
        """Mines an already checked-out repository."""
        index = await asyncio.to_thread(CodeIndexer().build, workspace)
        existing = existing + await _rules_in_file(workspace)
        paths = await _tracked_paths(workspace)

        brief = await _brief(workspace, index, paths, existing)
        executor = ToolExecutor(RetrievalTools(workspace, index, []))
        run = await run_rule_miner(self._provider, brief, executor)

        result = MiningResult(stop_reason=run.stop_reason, cost_usd=run.cost_usd, error=run.error)
        if run.output is not None:
            result.suggestions, result.discarded = _validate(run.output, workspace, paths, existing)
        logger.info("rule mining: %d suggested, %d discarded, %s, $%s", len(result.suggestions),
                    len(result.discarded), run.stop_reason,
                    f"{run.cost_usd:.5f}" if run.cost_usd is not None else "unknown")
        return result


def _validate(output: RuleMinerOutput, workspace: Path, paths: list[str],
              existing: list[BusinessRule]) -> tuple[list[BusinessRule], list[str]]:
    """Keeps suggestions whose source is really in the repository and whose scope matches real files."""
    files = FileCache(workspace)
    taken = {r.rule_id for r in existing}
    kept: list[BusinessRule] = []
    discarded: list[str] = []

    for suggestion in output.suggestions:
        if suggestion.rule_id in taken:
            discarded.append(f"{suggestion.rule_id}: id already used")
            continue
        problem, verified = check_evidence(suggestion.source, files, [])
        if problem or not verified:
            discarded.append(f"{suggestion.rule_id}: source not verified ({problem or 'nothing to check'})")
            continue
        scope = [g for g in suggestion.applies_to if any(glob_match(p, g) for p in paths)]
        if suggestion.applies_to and not scope:
            discarded.append(f"{suggestion.rule_id}: applies_to matches no file ({', '.join(suggestion.applies_to)})")
            continue

        source = suggestion.source
        kept.append(BusinessRule(
            rule_id=suggestion.rule_id, rule=suggestion.rule, applies_to=scope, severity=suggestion.severity,
            rationale=suggestion.rationale, source="suggested", status="suggested",
            evidence=(f"{source.ref}: {source.quote}" if source.quote else source.ref)[:700],
        ))
        taken.add(suggestion.rule_id)
    return kept, discarded


async def _brief(workspace: Path, index: RepoIndex, paths: list[str], existing: list[BusinessRule]) -> str:
    parts = ["## Files", "\n".join(paths[:_MAX_PATHS])]
    if len(paths) > _MAX_PATHS:
        parts.append(f"[{len(paths) - _MAX_PATHS} more files not listed]")

    readme = next((p for p in paths if p.lower() in ("readme.md", "readme.rst", "readme.txt", "readme")), None)
    if readme:
        parts += ["", f"## {readme}", _read(workspace, readme, _README_CHARS)]
    docs = [p for p in paths if p.lower().startswith("docs/") and p.lower().endswith((".md", ".rst", ".txt"))]
    for doc in docs[:_MAX_DOCS]:
        parts += ["", f"## {doc}", _read(workspace, doc, _DOC_CHARS)]

    tests = sorted({f"{s.file_path}::{s.qualified_name}" for s in index.symbols
                    if is_test_file(s.file_path) and s.name.lower().startswith("test")})
    if tests:
        parts += ["", "## Test names", "\n".join(tests[:_MAX_TEST_NAMES])]

    constraints = await _constraint_lines(workspace)
    if constraints:
        parts += ["", "## Lines that state constraints", constraints]

    if existing:
        parts += ["", "## Rules that already exist (don't repeat them)",
                  "\n".join(f"[{r.rule_id}] {r.rule}" for r in existing[:60])]
    return redact("\n".join(parts))


async def _constraint_lines(workspace: Path) -> str:
    code, output = await run_git_output(
        ["-c", "core.quotePath=false", "grep", "-n", "-I", "-i", "-E", "--max-count=4", "-e", _CONSTRAINT_WORDS,
         "--", "."], workspace, settings.git_clone_timeout_seconds, allowed_exit_codes=(0, 1),
    )
    if code != 0:
        return ""
    lines = [line[:240] for line in output.decode("utf-8", errors="replace").splitlines()
             if not is_generated(line.split(":", 1)[0]) and not is_secret_file(line.split(":", 1)[0])
             and not line.startswith(RULES_FILES)]
    return "\n".join(lines[:_MAX_CONSTRAINT_LINES])


async def _tracked_paths(workspace: Path) -> list[str]:
    _code, output = await run_git_output(["-c", "core.quotePath=false", "ls-files"], workspace,
                                         settings.git_clone_timeout_seconds)
    return [p for p in output.decode("utf-8", errors="replace").splitlines()
            if p and not is_generated(p) and not is_secret_file(p)]


async def _rules_in_file(workspace: Path) -> list[BusinessRule]:
    for name in RULES_FILES:
        path = workspace / name
        if path.is_file() and not path.is_symlink():
            return parse_rules_file(path.read_text(encoding="utf-8", errors="replace")[:64_000])[0]
    return []


async def _branch_head(clone_url: str, branch: str, git_env: dict[str, str]) -> str | None:
    with tempfile.TemporaryDirectory() as scratch:
        code, output = await run_git_output(["ls-remote", clone_url, f"refs/heads/{branch}"], Path(scratch),
                                            settings.git_clone_timeout_seconds, allowed_exit_codes=(0, 2),
                                            env=git_env)
    text = output.decode().split()
    return text[0] if code == 0 and text else None


def _read(workspace: Path, path: str, limit: int) -> str:
    lines = FileCache(workspace).lines(path)
    text = "\n".join(lines) if lines else ""
    return text[:limit] + ("\n[truncated]" if len(text) > limit else "")
