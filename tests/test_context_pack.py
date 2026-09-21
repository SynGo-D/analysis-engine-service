import asyncio
import subprocess
from pathlib import Path

from analysis_engine.context import ContextLimits, ContextPackBuilder
from analysis_engine.diffing import DiffExtractor, changed_symbols, mark_findings
from analysis_engine.domain import AnalysisJob, Finding
from analysis_engine.indexing import CodeIndexer
from analysis_engine.retrieval import REDACTED

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**_ENV, "HOME": str(cwd)}).stdout


def _write(repo: Path, name: str, content: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(content)


def _setup(tmp_path: Path, feature_files: dict[str, str], base_files: dict[str, str] | None = None) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _write(origin, "billing/total.py", "def total(price, discount):\n    return price * 1.2 - discount\n\n\ndef checkout(cart):\n    return total(cart.price, cart.discount)\n")
    for name, content in (base_files or {}).items():
        _write(origin, name, content)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "base")
    _git(origin, "checkout", "-q", "-b", "feature")
    for name, content in feature_files.items():
        _write(origin, name, content)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "feature")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git(workspace, "init", "-q")
    _git(workspace, "remote", "add", "origin", f"file://{origin}")
    _git(workspace, "fetch", "-q", "--depth", "50", "origin", "--", "feature")
    _git(workspace, "checkout", "-q", "FETCH_HEAD")
    return workspace


_JOB = AnalysisJob(provider="github", repository="acme/shop", clone_url="https://github.com/acme/shop.git",
                   commit_sha="a" * 40, branch="feature", pull_request_number=7, queued_at="now",
                   target_branch="main", title="Apply discount before tax",
                   description="Discounts must be applied before tax.")

_DISCOUNT_FIX = {"billing/total.py": "def total(price, discount):\n    return (price - discount) * 1.2\n\n\ndef checkout(cart):\n    return total(cart.price, cart.discount)\n"}


def _pack(workspace: Path, findings: list[Finding] | None = None, limits: ContextLimits | None = None):
    changes = asyncio.run(DiffExtractor(timeout=30).extract(workspace, _JOB))
    index = CodeIndexer().build(workspace)
    changes.changed_symbols = changed_symbols(index, changes.change_set)
    marked, _ = mark_findings(findings or [], changes.change_set)
    return asyncio.run(ContextPackBuilder(limits=limits, timeout=30).build(workspace, _JOB, changes, index, marked))


def _finding(path: str, line: int, message: str) -> Finding:
    return Finding(repository="acme/shop", pull_request_number=7, commit_sha="h", file_path=path, line=line,
                   severity="warning", category="code_smell", rule_id="R0913", message=message, tool="pylint",
                   fingerprint="f" * 64)


def test_contains_the_pr_intent_diff_changed_function_callers_and_findings(tmp_path):
    workspace = _setup(tmp_path, _DISCOUNT_FIX)
    pack = _pack(workspace, [_finding("billing/total.py", 2, "changed-line finding"),
                             _finding("billing/total.py", 5, "untouched-line finding")])
    text = pack.render()

    assert "Title: Apply discount before tax" in text
    assert "Discounts must be applied before tax." in text
    assert "+    return (price - discount) * 1.2" in text                # the diff
    assert "function total — billing/total.py:1-2" in text               # full source of the changed function
    assert "total is called by:\n  checkout at billing/total.py:6" in text
    assert "changed-line finding" in text
    # Linter findings on untouched lines stay out: the agent can fetch
    # them, but it doesn't pay for them by default.
    assert "untouched-line finding" not in text


def test_the_pack_is_small_for_a_small_pr(tmp_path):
    pack = _pack(_setup(tmp_path, _DISCOUNT_FIX))

    assert pack.estimated_tokens < 1_000


def test_secrets_in_the_diff_never_reach_the_pack(tmp_path):
    files = dict(_DISCOUNT_FIX)
    files["billing/config.py"] = 'STRIPE_KEY = "sk-proj-' + "z" * 40 + '"\n'
    text = _pack(_setup(tmp_path, files)).render()

    assert "z" * 40 not in text
    assert REDACTED in text


def test_diff_budget_drops_whole_files_and_says_which(tmp_path):
    files = dict(_DISCOUNT_FIX)
    files["billing/big.py"] = "".join(f"value_{i} = {i}\n" for i in range(400))
    limits = ContextLimits(diff_chars=1_500)

    pack = _pack(_setup(tmp_path, files), limits=limits)
    diff = next(s for s in pack.sections if s.title == "Diff")

    # The small, central change survives; the big file is named, not shown.
    assert "(price - discount)" in diff.body
    assert "value_399" not in diff.body
    assert "billing/big.py" in diff.omitted
    assert "read_file" in diff.omitted


def test_a_single_huge_file_diff_is_cut_with_a_pointer(tmp_path):
    files = {"billing/big.py": "".join(f"value_{i} = {i}\n" for i in range(2_000))}
    limits = ContextLimits(diff_file_chars=2_000)

    diff = next(s for s in _pack(_setup(tmp_path, files), limits=limits).sections if s.title == "Diff")

    assert "[Rest of this file's diff omitted. Use read_file('billing/big.py').]" in diff.body


def test_tests_come_after_source_in_the_diff(tmp_path):
    files = dict(_DISCOUNT_FIX)
    files["tests/test_total.py"] = "def test_a():\n    assert True\n"
    diff = next(s for s in _pack(_setup(tmp_path, files)).sections if s.title == "Diff").body

    assert diff.index("billing/total.py") < diff.index("tests/test_total.py")


def test_symbol_budget_prefers_functions_with_callers(tmp_path):
    base = {"billing/extra.py": "".join(f"def helper_{i}():\n    return {i}\n\n\n" for i in range(30))}
    files = dict(_DISCOUNT_FIX)
    files["billing/extra.py"] = "".join(f"def helper_{i}():\n    return {i + 100}\n\n\n" for i in range(30))
    limits = ContextLimits(symbols_chars=400)

    section = next(s for s in _pack(_setup(tmp_path, files, base), limits=limits).sections
                   if s.title.startswith("Edited functions"))

    # total() has a caller, so it's first in line for the budget.
    assert "function total" in section.body
    assert "more changed symbol(s) not shown" in section.omitted


def test_brand_new_functions_are_not_repeated_after_the_diff(tmp_path):
    files = dict(_DISCOUNT_FIX)
    files["billing/new.py"] = "def brand_new():\n    return 1\n"
    pack = _pack(_setup(tmp_path, files))
    section = next(s for s in pack.sections if s.title.startswith("Edited functions"))

    assert "function total" in section.body          # edited: needs full source
    assert "brand_new" not in section.body            # new: already whole in the diff
    assert "+def brand_new():" in pack.render()
