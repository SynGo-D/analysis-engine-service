from pathlib import Path

from analysis_engine.analyzers.eslint_analyzer import EslintAnalyzer
from analysis_engine.domain import AnalysisJob
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


def test_to_finding_maps_severity_and_known_rule_category():
    analyzer = EslintAnalyzer()
    message = {
        "ruleId": "no-unused-vars",
        "severity": 2,
        "message": "'foo' is assigned a value but never used.",
        "line": 14,
        "column": 7,
    }

    finding = analyzer._to_finding(message, "src/service.ts", _job())

    assert finding.severity == "error"
    assert finding.category == "unused_code"
    assert finding.rule_id == "no-unused-vars"
    assert finding.file_path == "src/service.ts"
    assert finding.line == 14
    assert finding.column == 7
    assert finding.fingerprint == ""


def test_to_finding_warning_severity():
    analyzer = EslintAnalyzer()
    message = {"ruleId": "no-console", "severity": 1, "message": "Unexpected console statement.", "line": 1, "column": 1}

    finding = analyzer._to_finding(message, "src/index.ts", _job())

    assert finding.severity == "warning"
    # No explicit mapping for no-console — falls back to the default category.
    assert finding.category == "code_smell"


def test_to_finding_handles_fatal_parse_error_with_no_rule_id():
    analyzer = EslintAnalyzer()
    message = {"ruleId": None, "severity": 2, "message": "Parsing error: Unexpected token", "line": 3, "column": 5}

    finding = analyzer._to_finding(message, "src/broken.ts", _job())

    assert finding.rule_id == "parse-error"
    assert finding.category == "code_smell"


def test_to_finding_complexity_and_cognitive_complexity_categories_differ():
    analyzer = EslintAnalyzer()
    job = _job()

    complexity = analyzer._to_finding(
        {"ruleId": "complexity", "severity": 2, "message": "has a complexity of 12.", "line": 1, "column": 1},
        "src/a.ts",
        job,
    )
    cognitive = analyzer._to_finding(
        {"ruleId": "sonarjs/cognitive-complexity", "severity": 2, "message": "Cognitive Complexity from 20 to 15.", "line": 1, "column": 1},
        "src/a.ts",
        job,
    )

    assert complexity.category == "complexity"
    assert cognitive.category == "cognitive_complexity"
    assert complexity.category != cognitive.category


def test_relative_path_converts_absolute_eslint_path_to_workspace_relative():
    analyzer = EslintAnalyzer()
    workspace = Workspace(job_id=_job().job_id, path=Path("/tmp/analysis-workspace"))

    relative = analyzer._relative_path("/tmp/analysis-workspace/src/nested/file.ts", workspace)

    assert relative == "src/nested/file.ts"
