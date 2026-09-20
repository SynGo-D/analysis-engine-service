import json
from pathlib import Path
from uuid import uuid4

import pytest

from analysis_engine.analyzers.python import radon_analyzer as module
from analysis_engine.analyzers.python.radon_analyzer import RadonAnalyzer
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


def _fake_responses(cc="{}", mi="{}", raw="{}", hal="{}"):
    """Returns a fake run_process that dispatches on which radon subcommand ('cc'/'mi'/'raw'/'hal') is being invoked."""
    payloads = {"cc": cc, "mi": mi, "raw": raw, "hal": hal}

    async def fake_run_process(args, **kwargs):
        label = args[1]  # args = [radon_bin, <subcommand>, "--json", ...files]
        return ProcessResult(stdout=payloads[label], stderr="", returncode=0)

    return fake_run_process


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_complexity_function_becomes_a_finding(tmp_path, monkeypatch):
    cc = json.dumps({
        "src/service.py": [
            {"type": "function", "name": "process_data", "lineno": 42, "endline": 60, "complexity": 18, "rank": "C", "col_offset": 0, "closures": []},
        ]
    })
    raw = json.dumps({"src/service.py": {"loc": 60, "lloc": 40, "sloc": 40, "comments": 5, "multi": 0, "blank": 15, "single_comments": 5}})

    monkeypatch.setattr(module, "run_process", _fake_responses(cc=cc, raw=raw))
    analyzer = RadonAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "src/service.py"])

    assert run.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    assert len(run.complexity) == 1
    assert run.complexity[0].complexity == 18
    assert any(f.rule_id == "radon-cyclomatic-complexity" for f in run.findings)
    assert run.complexity_metrics.maximum_complexity == 18
    assert run.complexity_metrics.high_complexity_function_count == 1


@pytest.mark.asyncio
async def test_low_complexity_function_is_recorded_but_not_a_finding(tmp_path, monkeypatch):
    cc = json.dumps({"a.py": [{"type": "function", "name": "add", "lineno": 1, "endline": 2, "complexity": 2, "rank": "A", "col_offset": 0, "closures": []}]})

    monkeypatch.setattr(module, "run_process", _fake_responses(cc=cc))
    analyzer = RadonAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "a.py"])

    assert len(run.complexity) == 1
    assert run.findings == []
    assert run.status == AnalyzerRunStatus.SUCCESS


@pytest.mark.asyncio
async def test_low_maintainability_file_becomes_a_finding(tmp_path, monkeypatch):
    mi = json.dumps({"messy.py": {"mi": 22.3, "rank": "A"}})

    monkeypatch.setattr(module, "run_process", _fake_responses(mi=mi))
    analyzer = RadonAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "messy.py"])

    assert any(f.rule_id == "radon-maintainability-index" for f in run.findings)
    assert run.maintainability_metrics.low_maintainability_file_count == 1
    assert run.maintainability_metrics.minimum_maintainability_index == 22.3


@pytest.mark.asyncio
async def test_nested_closures_are_flattened_into_complexity_entries(tmp_path, monkeypatch):
    cc = json.dumps({
        "a.py": [
            {
                "type": "function", "name": "outer", "lineno": 1, "endline": 20, "complexity": 3, "rank": "A",
                "col_offset": 0,
                "closures": [
                    {"type": "function", "name": "inner", "lineno": 5, "endline": 10, "complexity": 2, "rank": "A", "col_offset": 4, "closures": []},
                ],
            },
        ]
    })

    monkeypatch.setattr(module, "run_process", _fake_responses(cc=cc))
    analyzer = RadonAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "a.py"])

    assert {e.object for e in run.complexity} == {"outer", "inner"}


@pytest.mark.asyncio
async def test_per_file_radon_error_becomes_a_finding_not_a_crash(tmp_path, monkeypatch):
    """Radon's own exit code is always 0 — per-file errors surface as {"error": ...} JSON entries instead."""
    cc = json.dumps({"broken.py": {"error": "invalid syntax (<unknown>, line 1)"}})

    monkeypatch.setattr(module, "run_process", _fake_responses(cc=cc))
    analyzer = RadonAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "broken.py"])

    assert run.status == AnalyzerRunStatus.COMPLETED_WITH_FINDINGS
    assert any(f.rule_id == "radon-cc" and "invalid syntax" in f.message for f in run.findings)


@pytest.mark.asyncio
async def test_halstead_failure_is_best_effort_and_does_not_fail_the_run(tmp_path, monkeypatch):
    async def fake_run_process(args, **kwargs):
        label = args[1]
        if label == "hal":
            raise ToolExecutionError("radon hal timed out after 120.0s.")
        return ProcessResult(stdout="{}", stderr="", returncode=0)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    analyzer = RadonAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "a.py"])

    assert run.status == AnalyzerRunStatus.SUCCESS  # hal failing alone doesn't fail the whole run
    assert run.halstead == []


@pytest.mark.asyncio
async def test_required_subcommand_timeout_fails_the_whole_run(tmp_path, monkeypatch):
    async def fake_run_process(args, **kwargs):
        label = args[1]
        if label == "cc":
            raise ToolExecutionError("radon cc timed out after 120.0s.")
        return ProcessResult(stdout="{}", stderr="", returncode=0)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    analyzer = RadonAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [tmp_path / "a.py"])

    assert run.status == AnalyzerRunStatus.TIMEOUT


@pytest.mark.asyncio
async def test_run_skips_execution_entirely_for_empty_file_list(tmp_path, monkeypatch):
    calls = []

    async def fake_run_process(*args, **kwargs):
        calls.append(args)
        return ProcessResult(stdout="{}", stderr="", returncode=0)

    monkeypatch.setattr(module, "run_process", fake_run_process)
    analyzer = RadonAnalyzer()

    run = await analyzer.run(_workspace(tmp_path), _job(), [])

    assert run.status == AnalyzerRunStatus.SUCCESS
    assert calls == []
