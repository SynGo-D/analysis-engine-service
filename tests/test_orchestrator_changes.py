import asyncio
from contextlib import asynccontextmanager

from analysis_engine.application.orchestrator import AnalysisOrchestrator
from analysis_engine.diffing import ON_CHANGED_LINE
from analysis_engine.domain import AnalysisJob, ChangedFile, ChangeSet, Finding, PullRequestChanges
from analysis_engine.workspace import Workspace


class _Workspaces:
    def __init__(self, path):
        self._path = path

    @asynccontextmanager
    async def prepare(self, job):
        yield Workspace(job_id=job.job_id, path=self._path)


class _Analyzer:
    tool_name = "fake"

    async def analyze(self, workspace, job):
        return [
            Finding(repository=job.repository, pull_request_number=1, commit_sha="h", file_path="a.py",
                    line=line, severity="warning", category="code_smell", rule_id=f"r{line}",
                    message="m", tool="fake", fingerprint="")
            for line in (2, 9)
        ]


class _Factory:
    def create_for_languages(self, languages):
        return [_Analyzer()]


class _Extractor:
    def __init__(self, outcome):
        self._outcome = outcome

    async def extract(self, workspace_path, job, git_env=None):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _job():
    return AnalysisJob(provider="github", repository="acme/shop", clone_url="https://github.com/acme/shop.git",
                       commit_sha="a" * 40, branch="feature", pull_request_number=1, queued_at="now",
                       target_branch="main")


def _run(tmp_path, extractor_outcome):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    orchestrator = AnalysisOrchestrator(
        workspace_manager=_Workspaces(tmp_path),
        analyzer_factory=_Factory(),
        diff_extractor=_Extractor(extractor_outcome),
    )
    return asyncio.run(orchestrator.run(_job()))


def test_a_crash_while_computing_changes_does_not_fail_the_analysis(tmp_path):
    result = _run(tmp_path, RuntimeError("bug in diffing"))

    assert result.status == "completed"
    assert len(result.findings) == 2
    assert result.changes.status == "unavailable"
    assert result.changes.unavailable_reason == "error"


def test_marks_findings_and_lists_changed_symbols_when_changes_are_available(tmp_path):
    change_set = ChangeSet(base_sha="b", head_sha="h", target_branch="main",
                           files=[ChangedFile(path="a.py", status="modified", added_lines=[2])])
    result = _run(tmp_path, PullRequestChanges(status="available", change_set=change_set,
                                                files_changed=1, lines_added=1))

    by_line = {f.line: f.metadata[ON_CHANGED_LINE] for f in result.findings}
    assert by_line == {2: True, 9: False}
    assert result.changes.findings_on_changed_lines == 1
    assert [s.qualified_name for s in result.changes.changed_symbols] == ["f"]


def test_unavailable_changes_leave_findings_unmarked(tmp_path):
    result = _run(tmp_path, PullRequestChanges(status="unavailable", unavailable_reason="no_target_branch"))

    assert all(ON_CHANGED_LINE not in f.metadata for f in result.findings)
    assert result.changes.unavailable_reason == "no_target_branch"
