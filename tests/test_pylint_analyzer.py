from pathlib import Path
from uuid import uuid4

import pytest

from analysis_engine.analyzers.python import pylint_analyzer as module
from analysis_engine.analyzers.python.pylint_analyzer import PylintAnalyzer
from analysis_engine.analyzers.process_runner import ProcessResult
from analysis_engine.domain import AnalysisJob, AnalyzerRunStatus
from analysis_engine.workspace.workspace_manager import Workspace


def _job() -> AnalysisJob:
    return AnalysisJob(
        provider="github", repository="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        commit_sha="a" * 40, branch="main", pull_request_number=1,
        queued_at="2026-01-01T00:00:00Z",
    )


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(job_id=uuid4(), path=tmp_path)


# ---------------------------------------------------------------------------
# Output parsing / normalization
# ---------------------------------------------------------------------------


def test_to_finding_maps_type_to_severity_and_category():
    analyzer = PylintAnalyzer()
    message = {
        "type": "convention", "module": "example", "obj": "foo",
        "line": 10, "column": 4, "endLine": 10, "endColumn": 12,
        "path": "example.py", "symbol": "missing-function-docstring",
        "message": "Missing function or method docstring", "message-id": "C0116",
    }

    finding = analyzer._to_finding(message, _job(), _workspace(Path("/tmp")))

    assert finding.severity == "info"
    assert finding.category == "style"
    assert finding.rule_id == "missing-function-docstring"
    assert finding.line == 10
    assert finding.end_line == 10
    assert finding.metadata["messageId"] == "C0116"
    assert finding.metadata["pylintType"] == "convention"
    assert finding.fingerprint == ""


@pytest.mark.parametrize(
    "pylint_type,expected_severity,expected_category",
    [
        ("fatal", "error", "bug"),
        ("error", "error", "bug"),
        ("warning", "warning", "code_smell"),
        ("refactor", "warning", "code_smell"),
        ("convention", "info", "style"),
    ],
)
def test_severity_and_category_mapping_covers_every_pylint_type(pylint_type, expected_severity, expected_category):
    analyzer = PylintAnalyzer()
    message = {"type": pylint_type, "path": "x.py", "symbol": "some-rule", "message": "msg"}

    finding = analyzer._to_finding(message, _job(), _workspace(Path("/tmp")))

    assert finding.severity == expected_severity
    assert finding.category == expected_category


def test_relative_path_converts_absolute_pylint_path():
    analyzer = PylintAnalyzer()
    workspace = Workspace(job_id=uuid4(), path=Path("/tmp/workspace"))

    assert analyzer._relative_path("/tmp/workspace/src/mod.py", workspace) == "src/mod.py"


# ---------------------------------------------------------------------------
# Execution status classification (analyzers/python/README.md's exit-code
# semantics) — via monkeypatched run_process, no real subprocess needed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_success_with_no_findings_on_clean_output(tmp_path, monkeypatch):
    async def fake_run_process(*args, **kwargs):
        return ProcessResult(stdout="[]", stderr="", returncode=0)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    analyzer = PylintAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "clean.py"])

    assert run.status == AnalyzerRunStatus.SUCCESS
    assert run.findings == []


@pytest.mark.asyncio
async def test_run_returns_completed_with_findings_despite_nonzero_exit(tmp_path, monkeypatch):
    """Pylint's bitmask exit code (e.g. 16 = convention issues found) must not be read as a crash."""
    raw = '[{"type": "convention", "path": "x.py", "line": 1, "column": 0, "symbol": "missing-module-docstring", "message": "Missing module docstring", "message-id": "C0114"}]'

    async def fake_run_process(*args, **kwargs):
        return ProcessResult(stdout=raw, stderr="", returncode=16)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    analyzer = PylintAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "x.py"])

    assert run.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    assert len(run.findings) == 1


@pytest.mark.asyncio
async def test_run_returns_execution_error_on_usage_error_exit(tmp_path, monkeypatch):
    async def fake_run_process(*args, **kwargs):
        return ProcessResult(stdout="", stderr="unrecognized argument", returncode=32)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    analyzer = PylintAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "x.py"])

    assert run.status == AnalyzerRunStatus.EXECUTION_ERROR
    assert "unrecognized argument" in run.error_message


@pytest.mark.asyncio
async def test_run_returns_execution_error_on_malformed_json(tmp_path, monkeypatch):
    async def fake_run_process(*args, **kwargs):
        return ProcessResult(stdout="not valid json {{{", stderr="", returncode=0)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    analyzer = PylintAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "x.py"])

    assert run.status == AnalyzerRunStatus.EXECUTION_ERROR
    assert "Malformed" in run.error_message


@pytest.mark.asyncio
async def test_run_returns_timeout_status(tmp_path, monkeypatch):
    from analysis_engine.analyzers.process_runner import ToolExecutionError

    async def fake_run_process(*args, **kwargs):
        raise ToolExecutionError("pylint timed out after 120.0s.")

    monkeypatch.setattr(module, "run_process", fake_run_process)
    analyzer = PylintAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "x.py"])

    assert run.status == AnalyzerRunStatus.TIMEOUT


@pytest.mark.asyncio
async def test_run_returns_execution_error_when_binary_missing(tmp_path, monkeypatch):
    async def fake_run_process(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "/nonexistent/pylint")

    monkeypatch.setattr(module, "run_process", fake_run_process)
    analyzer = PylintAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "x.py"])

    assert run.status == AnalyzerRunStatus.EXECUTION_ERROR


@pytest.mark.asyncio
async def test_run_skips_execution_entirely_for_empty_file_list(tmp_path, monkeypatch):
    """No Python files -> SUCCESS with no findings, no subprocess launched at all."""
    calls = []

    async def fake_run_process(*args, **kwargs):
        calls.append(args)
        return ProcessResult(stdout="[]", stderr="", returncode=0)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    analyzer = PylintAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [])

    assert run.status == AnalyzerRunStatus.SUCCESS
    assert calls == []
