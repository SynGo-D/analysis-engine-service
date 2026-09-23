import json
from pathlib import Path

import pytest

from analysis_engine.analyzers.java_analyzer import JavaAnalyzer
from analysis_engine.analyzers.process_runner import ProcessResult, ToolExecutionError
from analysis_engine.domain import AnalysisJob
from analysis_engine.factories.analyzer_factory import AnalyzerFactory
from analysis_engine.factories.language_detector import detect_languages
from analysis_engine.workspace.workspace_manager import Workspace


def _job() -> AnalysisJob:
    return AnalysisJob(
        provider="github",
        repository="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        commit_sha="a" * 40,
        branch="main",
        pull_request_number=1,
        queued_at="2026-01-01T00:00:00Z",
    )


def _violation(**overrides) -> dict:
    violation = {
        "rule": "CyclomaticComplexity",
        "priority": 3,
        "beginline": 42,
        "begincolumn": 5,
        "description": "The method 'transfer(...)' has a cyclomatic complexity of 14.",
    }
    violation.update(overrides)
    return violation


class TestFindingMapping:
    def test_maps_location_rule_and_category(self):
        finding = JavaAnalyzer()._to_finding(
            _violation(rule="UnusedLocalVariable", description="Avoid unused local variables such as 'x'."),
            "src/main/java/Service.java",
            _job(),
        )

        assert finding.rule_id == "UnusedLocalVariable"
        assert finding.category == "unused_code"
        assert finding.file_path == "src/main/java/Service.java"
        assert finding.line == 42
        assert finding.column == 5
        assert finding.tool == "pmd"
        # Left for FindingNormalizer, as with every other analyzer.
        assert finding.fingerprint == ""

    @pytest.mark.parametrize(
        ("priority", "severity"),
        [(1, "error"), (2, "error"), (3, "warning"), (4, "warning"), (5, "warning")],
    )
    def test_priority_one_and_two_are_errors(self, priority, severity):
        finding = JavaAnalyzer()._to_finding(_violation(priority=priority), "A.java", _job())
        assert finding.severity == severity

    def test_unmapped_rule_falls_back_to_code_smell(self):
        finding = JavaAnalyzer()._to_finding(_violation(rule="SomeRuleAddedLater"), "A.java", _job())
        assert finding.category == "code_smell"

    def test_missing_priority_is_treated_as_lowest(self):
        violation = _violation()
        del violation["priority"]

        assert JavaAnalyzer()._to_finding(violation, "A.java", _job()).severity == "warning"


class TestProcessHandling:
    """PMD exits 4 when it finds violations, which is the normal case."""

    @pytest.mark.asyncio
    async def test_exit_code_four_is_success(self, monkeypatch, tmp_path):
        report = {"files": [{"filename": str(tmp_path / "A.java"), "violations": [_violation()]}]}

        async def fake_run(*_args, **_kwargs):
            return ProcessResult(stdout=json.dumps(report), stderr="", returncode=4)

        monkeypatch.setattr("analysis_engine.analyzers.java_analyzer.run_process", fake_run)

        findings = await JavaAnalyzer().analyze(Workspace(job_id="j", path=tmp_path), _job())

        assert len(findings) == 1
        assert findings[0].file_path == "A.java"  # made relative to the workspace

    @pytest.mark.asyncio
    async def test_unexpected_exit_code_is_a_failure(self, monkeypatch, tmp_path):
        async def fake_run(*_args, **_kwargs):
            return ProcessResult(stdout="", stderr="boom", returncode=2)

        monkeypatch.setattr("analysis_engine.analyzers.java_analyzer.run_process", fake_run)

        with pytest.raises(ToolExecutionError):
            await JavaAnalyzer().analyze(Workspace(job_id="j", path=tmp_path), _job())

    @pytest.mark.asyncio
    async def test_output_that_is_not_json_is_a_failure(self, monkeypatch, tmp_path):
        async def fake_run(*_args, **_kwargs):
            return ProcessResult(stdout="[WARN] something", stderr="", returncode=0)

        monkeypatch.setattr("analysis_engine.analyzers.java_analyzer.run_process", fake_run)

        with pytest.raises(ToolExecutionError):
            await JavaAnalyzer().analyze(Workspace(job_id="j", path=tmp_path), _job())

    @pytest.mark.asyncio
    async def test_clean_run_produces_no_findings(self, monkeypatch, tmp_path):
        async def fake_run(*_args, **_kwargs):
            return ProcessResult(stdout=json.dumps({"files": []}), stderr="", returncode=0)

        monkeypatch.setattr("analysis_engine.analyzers.java_analyzer.run_process", fake_run)

        assert await JavaAnalyzer().analyze(Workspace(job_id="j", path=tmp_path), _job()) == []


class TestSelection:
    def test_java_files_select_the_java_analyzer(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Service.java").write_text("class Service {}\n")

        languages = detect_languages(tmp_path)
        tools = [a.tool_name for a in AnalyzerFactory().create_for_languages(languages)]

        assert languages == frozenset({"java"})
        assert tools == ["pmd"]

    def test_maven_output_is_not_mistaken_for_source(self, tmp_path: Path):
        """A committed target/ holds generated sources and copied dependencies."""
        (tmp_path / "target" / "classes").mkdir(parents=True)
        (tmp_path / "target" / "classes" / "Generated.java").write_text("class Generated {}\n")

        assert detect_languages(tmp_path) == frozenset()
