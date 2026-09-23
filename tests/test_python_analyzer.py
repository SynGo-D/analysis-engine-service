"""
Integration + failure-isolation tests for PythonAnalyzer.

Unlike test_pylint_analyzer.py/test_radon_analyzer.py/test_bandit_analyzer.py
(which monkeypatch run_process), the tests marked "integration" below
actually invoke the real pylint/radon/bandit binaries against the fixture
repository at tests/fixtures/python_project/ — this is the "run the actual
analyzers" requirement, not a simulation of it.
"""
from pathlib import Path
from uuid import uuid4

import pytest

from analysis_engine.analyzers.python.python_analyzer import PythonAnalyzer, discover_python_files
from analysis_engine.config import settings
from analysis_engine.domain import AnalysisJob, AnalyzerRunStatus
from analysis_engine.workspace.workspace_manager import Workspace

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "python_project"


def _job() -> AnalysisJob:
    return AnalysisJob(
        provider="github", repository="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        commit_sha="a" * 40, branch="main", pull_request_number=1,
        queued_at="2026-01-01T00:00:00Z",
    )


def _workspace(path: Path) -> Workspace:
    return Workspace(job_id=uuid4(), path=path)


# ---------------------------------------------------------------------------
# discover_python_files
# ---------------------------------------------------------------------------


def test_discover_python_files_finds_every_fixture_module():
    files = discover_python_files(FIXTURE_REPO)
    names = {f.name for f in files}
    assert names == {
        "clean.py", "pylint_issues.py", "high_complexity.py",
        "low_maintainability.py", "security_issues.py",
    }


def test_discover_python_files_skips_ignored_directories(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.py").write_text("x = 1\n")
    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "vendored.py").write_text("y = 2\n")

    files = discover_python_files(tmp_path)

    assert [f.name for f in files] == ["real.py"]


def test_discover_python_files_returns_empty_list_for_no_python(tmp_path):
    (tmp_path / "README.md").write_text("hello\n")
    assert discover_python_files(tmp_path) == []


# ---------------------------------------------------------------------------
# Integration — real pylint/radon/bandit subprocesses against the fixture repo
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pylint_findings_are_detected():
    analyzer = PythonAnalyzer()
    await analyzer.analyze(_workspace(FIXTURE_REPO), _job())

    pylint_run = analyzer.last_result.pylint
    assert pylint_run.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    rule_ids = {f.rule_id for f in pylint_run.findings}
    assert "unused-import" in rule_ids  # from pylint_issues.py
    assert not any(f.file_path.endswith("clean.py") for f in pylint_run.findings)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_radon_complexity_is_detected():
    analyzer = PythonAnalyzer()
    await analyzer.analyze(_workspace(FIXTURE_REPO), _job())

    radon_run = analyzer.last_result.radon
    assert any(e.file.endswith("high_complexity.py") and e.complexity >= 11 for e in radon_run.complexity)
    assert radon_run.complexity_metrics.high_complexity_function_count >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_radon_maintainability_is_detected():
    analyzer = PythonAnalyzer()
    await analyzer.analyze(_workspace(FIXTURE_REPO), _job())

    radon_run = analyzer.last_result.radon
    low_mi_files = {e.file for e in radon_run.maintainability if e.mi < 65}
    assert any(f.endswith("low_maintainability.py") for f in low_mi_files)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bandit_security_findings_are_detected():
    analyzer = PythonAnalyzer()
    await analyzer.analyze(_workspace(FIXTURE_REPO), _job())

    bandit_run = analyzer.last_result.bandit
    assert bandit_run.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    rule_ids = {f.rule_id for f in bandit_run.findings}
    assert "B105" in rule_ids  # hardcoded password, from security_issues.py


@pytest.mark.integration
@pytest.mark.asyncio
async def test_clean_file_produces_no_findings_from_any_tool():
    analyzer = PythonAnalyzer()
    await analyzer.analyze(_workspace(FIXTURE_REPO), _job())

    result = analyzer.last_result
    all_findings = result.pylint.findings + result.radon.findings + result.bandit.findings
    assert not any(f.file_path.endswith("clean.py") for f in all_findings)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_loc_is_sourced_from_radon_raw_not_recounted():
    analyzer = PythonAnalyzer()
    await analyzer.analyze(_workspace(FIXTURE_REPO), _job())

    loc = analyzer.last_result.metrics.loc
    assert loc.physical_loc > 0
    assert loc.kloc == loc.physical_loc / 1000


# ---------------------------------------------------------------------------
# Failure isolation / edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_repository_produces_success_with_no_findings(tmp_path):
    analyzer = PythonAnalyzer()
    findings = await analyzer.analyze(_workspace(tmp_path), _job())

    assert findings == []
    assert analyzer.last_result.pylint.status == AnalyzerRunStatus.SUCCESS
    assert analyzer.last_result.radon.status == AnalyzerRunStatus.SUCCESS
    assert analyzer.last_result.bandit.status == AnalyzerRunStatus.SUCCESS


@pytest.mark.asyncio
async def test_repository_with_no_python_files_produces_success_with_no_findings(tmp_path):
    (tmp_path / "README.md").write_text("nothing to analyze here\n")

    analyzer = PythonAnalyzer()
    findings = await analyzer.analyze(_workspace(tmp_path), _job())

    assert findings == []


@pytest.mark.asyncio
async def test_one_tool_failing_does_not_prevent_the_others_from_running(monkeypatch):
    """pylint's binary missing -> pylint EXECUTION_ERROR, but radon/bandit still run and return real findings."""
    monkeypatch.setattr(settings, "pylint_bin_path", "/nonexistent/pylint")

    analyzer = PythonAnalyzer()
    findings = await analyzer.analyze(_workspace(FIXTURE_REPO), _job())

    result = analyzer.last_result
    assert result.pylint.status == AnalyzerRunStatus.EXECUTION_ERROR
    assert result.radon.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    assert result.bandit.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    # Findings returned to the orchestrator are exactly radon's + bandit's — pylint contributed none.
    assert findings == result.radon.findings + result.bandit.findings


@pytest.mark.asyncio
async def test_all_tools_failing_raises_so_the_orchestrator_marks_the_job_failed(monkeypatch, tmp_path):
    (tmp_path / "broken.py").write_text("x = 1\n")
    monkeypatch.setattr(settings, "pylint_bin_path", "/nonexistent/pylint")
    monkeypatch.setattr(settings, "radon_bin_path", "/nonexistent/radon")
    monkeypatch.setattr(settings, "bandit_bin_path", "/nonexistent/bandit")

    analyzer = PythonAnalyzer()
    with pytest.raises(RuntimeError, match="All Python analyzers failed"):
        await analyzer.analyze(_workspace(tmp_path), _job())

    # last_result is still populated (as a side effect, before the raise) —
    # the orchestrator can still see *why* each tool failed.
    assert analyzer.last_result is not None
    assert analyzer.last_result.pylint.status == AnalyzerRunStatus.EXECUTION_ERROR


@pytest.mark.asyncio
async def test_effectively_zero_timeout_times_out_every_tool_and_raises(monkeypatch, tmp_path):
    """Not an isolation case — a near-zero timeout genuinely can't be met by any subprocess launch, so all three time out and the composite's own all-failed policy fires, same as test_all_tools_failing_raises... above but via TIMEOUT instead of EXECUTION_ERROR."""
    (tmp_path / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(settings, "python_analyzer_timeout_seconds", 0.0001)

    analyzer = PythonAnalyzer()
    with pytest.raises(RuntimeError, match="All Python analyzers failed"):
        await analyzer.analyze(_workspace(tmp_path), _job())

    result = analyzer.last_result
    assert result.pylint.status == AnalyzerRunStatus.TIMEOUT
    assert result.radon.status == AnalyzerRunStatus.TIMEOUT
    assert result.bandit.status == AnalyzerRunStatus.TIMEOUT


@pytest.mark.asyncio
async def test_one_tool_timing_out_does_not_prevent_the_others_from_completing(monkeypatch):
    """Deterministic isolation test: pylint alone times out (mocked), radon/bandit run for real against the fixture repo."""
    from analysis_engine.analyzers.python import pylint_analyzer as pylint_module
    from analysis_engine.analyzers.process_runner import ToolExecutionError

    async def fake_pylint_run_process(*args, **kwargs):
        raise ToolExecutionError("pylint timed out after 120.0s.")

    monkeypatch.setattr(pylint_module, "run_process", fake_pylint_run_process)

    analyzer = PythonAnalyzer()
    findings = await analyzer.analyze(_workspace(FIXTURE_REPO), _job())

    result = analyzer.last_result
    assert result.pylint.status == AnalyzerRunStatus.TIMEOUT
    assert result.radon.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    assert result.bandit.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    assert findings == result.radon.findings + result.bandit.findings
