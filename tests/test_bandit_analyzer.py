import json
from pathlib import Path
from uuid import uuid4

import pytest

from analysis_engine.analyzers.python import bandit_analyzer as module
from analysis_engine.analyzers.python.bandit_analyzer import BanditAnalyzer
from analysis_engine.analyzers.process_runner import ProcessResult, ToolExecutionError
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


_SAMPLE_RESULT = {
    "filename": "./config.py",
    "line_number": 15,
    "line_range": [15],
    "col_offset": 11,
    "end_col_offset": 20,
    "test_id": "B105",
    "test_name": "hardcoded_password_string",
    "issue_text": "Possible hardcoded password: 'hunter2'",
    "issue_severity": "LOW",
    "issue_confidence": "MEDIUM",
}


def test_to_finding_maps_fields_and_uses_vulnerability_category(tmp_path):
    analyzer = BanditAnalyzer()
    finding = analyzer._to_finding(_SAMPLE_RESULT, _job(), _workspace(tmp_path))

    assert finding.category == "vulnerability"
    assert finding.severity == "info"  # LOW -> info
    assert finding.rule_id == "B105"
    assert finding.message == "Possible hardcoded password: 'hunter2'"
    assert finding.line == 15
    assert finding.column == 11
    assert finding.metadata["confidence"] == "MEDIUM"
    assert finding.metadata["testName"] == "hardcoded_password_string"


@pytest.mark.parametrize(
    "bandit_severity,expected",
    [("HIGH", "error"), ("MEDIUM", "warning"), ("LOW", "info")],
)
def test_severity_mapping(tmp_path, bandit_severity, expected):
    analyzer = BanditAnalyzer()
    item = {**_SAMPLE_RESULT, "issue_severity": bandit_severity}
    finding = analyzer._to_finding(item, _job(), _workspace(tmp_path))
    assert finding.severity == expected


def test_relative_path_resolves_relative_to_workspace():
    analyzer = BanditAnalyzer()
    workspace = Workspace(job_id=uuid4(), path=Path("/tmp/workspace"))
    # Bandit reports filenames as "./relative/path.py" when run with cwd=workspace.
    result = analyzer._relative_path("/tmp/workspace/src/database.py", workspace)
    assert result == "src/database.py"


@pytest.mark.asyncio
async def test_run_returns_success_on_no_findings(tmp_path, monkeypatch):
    async def fake_run_process(*args, **kwargs):
        return ProcessResult(stdout=json.dumps({"results": [], "errors": []}), stderr="", returncode=0)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    run = await BanditAnalyzer().run(_workspace(tmp_path), _job(), [tmp_path / "clean.py"])

    assert run.status == AnalyzerRunStatus.SUCCESS
    assert run.findings == []


@pytest.mark.asyncio
async def test_run_returns_completed_with_findings_on_exit_code_1(tmp_path, monkeypatch):
    """Bandit exits 1 when it finds issues — not a crash."""
    async def fake_run_process(*args, **kwargs):
        return ProcessResult(stdout=json.dumps({"results": [_SAMPLE_RESULT], "errors": []}), stderr="", returncode=1)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    run = await BanditAnalyzer().run(_workspace(tmp_path), _job(), [tmp_path / "config.py"])

    assert run.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    assert len(run.findings) == 1
    assert run.metrics.total_issues == 1
    assert run.metrics.low_severity == 1


@pytest.mark.asyncio
async def test_run_surfaces_per_file_parse_errors_as_findings(tmp_path, monkeypatch):
    report = {"results": [], "errors": [{"filename": "./broken.py", "reason": "syntax error while parsing AST from file"}]}

    async def fake_run_process(*args, **kwargs):
        return ProcessResult(stdout=json.dumps(report), stderr="", returncode=0)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    run = await BanditAnalyzer().run(_workspace(tmp_path), _job(), [tmp_path / "broken.py"])

    assert run.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    assert run.findings[0].rule_id == "bandit-parse-error"
    # Parse-error findings aren't security findings, so they don't count toward BanditMetrics.
    assert run.metrics.total_issues == 0


@pytest.mark.asyncio
async def test_run_returns_execution_error_on_unexpected_exit_code(tmp_path, monkeypatch):
    async def fake_run_process(*args, **kwargs):
        return ProcessResult(stdout="", stderr="bandit: error: unrecognized arguments", returncode=2)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    run = await BanditAnalyzer().run(_workspace(tmp_path), _job(), [tmp_path / "x.py"])

    assert run.status == AnalyzerRunStatus.EXECUTION_ERROR


@pytest.mark.asyncio
async def test_run_returns_execution_error_on_malformed_json(tmp_path, monkeypatch):
    async def fake_run_process(*args, **kwargs):
        return ProcessResult(stdout="{not json", stderr="", returncode=0)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    run = await BanditAnalyzer().run(_workspace(tmp_path), _job(), [tmp_path / "x.py"])

    assert run.status == AnalyzerRunStatus.EXECUTION_ERROR


@pytest.mark.asyncio
async def test_run_returns_timeout_status(tmp_path, monkeypatch):
    async def fake_run_process(*args, **kwargs):
        raise ToolExecutionError("bandit timed out after 120.0s.")

    monkeypatch.setattr(module, "run_process", fake_run_process)
    run = await BanditAnalyzer().run(_workspace(tmp_path), _job(), [tmp_path / "x.py"])

    assert run.status == AnalyzerRunStatus.TIMEOUT


@pytest.mark.asyncio
async def test_run_returns_execution_error_when_binary_missing(tmp_path, monkeypatch):
    async def fake_run_process(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "/nonexistent/bandit")

    monkeypatch.setattr(module, "run_process", fake_run_process)
    run = await BanditAnalyzer().run(_workspace(tmp_path), _job(), [tmp_path / "x.py"])

    assert run.status == AnalyzerRunStatus.EXECUTION_ERROR
