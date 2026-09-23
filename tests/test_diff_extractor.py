import asyncio
import subprocess
from pathlib import Path

import pytest

from analysis_engine.diffing import DiffExtractor, diff_extractor
from analysis_engine.domain import AnalysisJob

# These tests run real git against a local "origin" repository, because
# the facts under test — merge bases, shallow fetches, rename detection,
# how git quotes odd filenames — are git's behaviour, not ours. A mocked
# git would only test our assumptions about it.


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd), "PATH": "/usr/bin:/bin"},
    ).stdout


def _write(repo: Path, name: str, content: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def origin(tmp_path) -> Path:
    """A repository with a `main` branch and a `feature` branch off it."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "app/pricing.py", "def total(price, discount):\n    taxed = price * 1.2\n    return taxed - discount\n\n\ndef unchanged():\n    return 1\n")
    _write(repo, "app/legacy.py", "def old():\n    return 0\n")
    _write(repo, "app/rename_me.py", "def stays_the_same():\n    return 'same content'\n")
    _write(repo, "app/service.py", "def run():\n    a = 1\n    b = 2\n    return a\n")
    _commit(repo, "base")

    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo, "app/pricing.py", "def total(price, discount):\n    discounted = price - discount\n    return discounted * 1.2\n\n\ndef unchanged():\n    return 1\n")
    _write(repo, "app/new_module.py", "def added():\n    return 2\n")
    (repo / "app/legacy.py").unlink()
    _git(repo, "mv", "app/rename_me.py", "app/renamed.py")
    _write(repo, "package-lock.json", "{\n" + "\n".join(f'  "k{i}": {i},' for i in range(500)) + "\n}\n")
    _commit(repo, "feature work")

    # main moves on after the branch point. None of this is the PR's work,
    # and diffing against main's tip instead of the merge base would
    # wrongly report it as changed.
    _git(repo, "checkout", "-q", "main")
    _write(repo, "app/other.py", "def merged_elsewhere():\n    return 3\n")
    _commit(repo, "unrelated work on main")
    _git(repo, "checkout", "-q", "feature")
    return repo


def _workspace(tmp_path: Path, origin: Path, branch: str = "feature", depth: int = 50) -> Path:
    """Reproduces what clone_commit does, against the local origin."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git(workspace, "init", "-q")
    # file:// rather than a bare path: git ignores --depth for plain-path
    # remotes, which would hide every shallow-history problem.
    _git(workspace, "remote", "add", "origin", f"file://{origin}")
    _git(workspace, "fetch", "-q", "--depth", str(depth), "origin", "--", branch)
    _git(workspace, "checkout", "-q", "FETCH_HEAD")
    return workspace


def _job(target_branch: str | None = "main", branch: str = "feature") -> AnalysisJob:
    return AnalysisJob(
        provider="github", repository="acme/shop", clone_url="https://github.com/acme/shop.git",
        commit_sha="0" * 40, branch=branch, pull_request_number=1, queued_at="now",
        target_branch=target_branch,
    )


def _extract(workspace: Path, job: AnalysisJob, **kwargs):
    return asyncio.run(DiffExtractor(timeout=30, **kwargs).extract(workspace, job))


# ---------------------------------------------------------------------------


def test_reports_the_prs_own_changes_against_the_merge_base(tmp_path, origin):
    changes = _extract(_workspace(tmp_path, origin), _job())

    assert changes.status == "available"
    by_path = {f.path: f for f in changes.change_set.files}

    # main's later commit is not part of this PR.
    assert "app/other.py" not in by_path
    assert by_path["app/pricing.py"].status == "modified"
    assert by_path["app/pricing.py"].added_lines == [2, 3]
    assert by_path["app/new_module.py"].status == "added"
    assert by_path["app/new_module.py"].added_lines == [1, 2]
    assert by_path["app/legacy.py"].status == "deleted"


def test_detects_a_rename_without_reporting_its_lines_as_changed(tmp_path, origin):
    changes = _extract(_workspace(tmp_path, origin), _job())
    renamed = changes.change_set.file("app/renamed.py")

    assert renamed.status == "renamed"
    assert renamed.old_path == "app/rename_me.py"
    assert renamed.added_lines == []


def test_leaves_lockfiles_out_of_the_change_set(tmp_path, origin):
    changes = _extract(_workspace(tmp_path, origin), _job())

    assert changes.change_set.file("package-lock.json") is None
    assert changes.change_set.excluded_files == ["package-lock.json"]
    # 500 lockfile lines would otherwise dominate the totals.
    assert changes.lines_added < 20


def test_records_the_merge_base_and_head(tmp_path, origin):
    workspace = _workspace(tmp_path, origin)
    changes = _extract(workspace, _job())

    assert changes.change_set.head_sha == _git(workspace, "rev-parse", "HEAD").strip()
    assert changes.change_set.base_sha == _git(origin, "merge-base", "main", "feature").strip()
    assert changes.change_set.target_branch == "main"


def test_marks_a_pure_deletion_at_the_line_before_it(tmp_path, origin):
    # Removing "b = 2" (line 3) leaves no new line behind. The deletion is
    # recorded at line 2, still inside run(), so the change can be mapped
    # to that function.
    _write(origin, "app/service.py", "def run():\n    a = 1\n    return a\n")
    _commit(origin, "remove a line")

    changes = _extract(_workspace(tmp_path, origin), _job())
    service = changes.change_set.file("app/service.py")

    assert service.added_lines == []
    assert service.deletion_points == [2]
    assert service.lines_removed == 1


def test_handles_filenames_git_has_to_quote(tmp_path, origin):
    _write(origin, "app/with space.py", "x = 1\n")
    _write(origin, "app/unicodé.py", "y = 2\n")
    _write(origin, 'app/quote"d.py', "z = 3\n")
    _commit(origin, "odd names")

    changes = _extract(_workspace(tmp_path, origin), _job())
    by_path = {f.path: f for f in changes.change_set.files}

    assert by_path["app/with space.py"].added_lines == [1]
    assert by_path["app/unicodé.py"].added_lines == [1]
    assert by_path['app/quote"d.py'].added_lines == [1]


def test_a_removed_line_that_looks_like_a_diff_header_is_not_misread(tmp_path, origin):
    # "-- comment" removed shows up in the patch as "--- comment".
    _write(origin, "app/sql.py", "QUERY = '''\n-- comment\nSELECT 1\n'''\n")
    _commit(origin, "add sql")
    _git(origin, "checkout", "-q", "main")
    _git(origin, "merge", "-q", "--no-ff", "feature", "-m", "merge")
    _git(origin, "checkout", "-q", "-b", "feature2")
    _write(origin, "app/sql.py", "QUERY = '''\nSELECT 1\n'''\n")
    _write(origin, "app/after.py", "a = 1\n")
    _commit(origin, "remove comment")

    changes = _extract(_workspace(tmp_path, origin, branch="feature2"), _job(branch="feature2"))
    by_path = {f.path: f for f in changes.change_set.files}

    assert set(by_path) == {"app/sql.py", "app/after.py"}
    assert by_path["app/sql.py"].deletion_points == [1]
    assert by_path["app/after.py"].added_lines == [1]


# ---------------------------------------------------------------------------
# When the changes can't be worked out
# ---------------------------------------------------------------------------


def test_without_a_target_branch_changes_are_unavailable(tmp_path, origin):
    changes = _extract(_workspace(tmp_path, origin), _job(target_branch=None))

    assert changes.status == "unavailable"
    assert changes.unavailable_reason == "no_target_branch"


def test_a_target_branch_that_does_not_exist_is_an_error_not_a_crash(tmp_path, origin):
    changes = _extract(_workspace(tmp_path, origin), _job(target_branch="does-not-exist"))

    assert changes.unavailable_reason == "error"


def test_rejects_a_target_branch_that_could_inject_a_git_option(tmp_path, origin):
    changes = _extract(_workspace(tmp_path, origin), _job(target_branch="--upload-pack=touch /tmp/x"))

    assert changes.unavailable_reason == "error"


@pytest.fixture
def distant_origin(tmp_path) -> Path:
    """main and feature each have 6 commits after the branch point."""
    repo = tmp_path / "distant"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "a.py", "a = 0\n")
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    for i in range(6):
        _write(repo, "f.py", f"f = {i}\n")
        _commit(repo, f"feature {i}")
    _git(repo, "checkout", "-q", "main")
    for i in range(6):
        _write(repo, "m.py", f"m = {i}\n")
        _commit(repo, f"main {i}")
    _git(repo, "checkout", "-q", "feature")
    return repo


def test_fetches_deeper_when_the_branch_point_is_beyond_the_first_depth(tmp_path, distant_origin):
    workspace = _workspace(tmp_path, distant_origin, depth=2)

    changes = _extract(workspace, _job(), fetch_depths=(2, 20))

    assert changes.status == "available"
    assert [f.path for f in changes.change_set.files] == ["f.py"]


def test_gives_up_cleanly_when_no_merge_base_is_within_reach(tmp_path, distant_origin):
    workspace = _workspace(tmp_path, distant_origin, depth=2)

    changes = _extract(workspace, _job(), fetch_depths=(2, 3))

    assert changes.status == "unavailable"
    assert changes.unavailable_reason == "no_merge_base"


def test_a_huge_pr_keeps_its_file_list_but_skips_line_level_detail(tmp_path, origin, monkeypatch):
    monkeypatch.setattr(diff_extractor, "MAX_LINE_LEVEL_CHANGES", 3)

    changes = _extract(_workspace(tmp_path, origin), _job())

    assert changes.status == "unavailable"
    assert changes.unavailable_reason == "too_large"
    assert changes.files_changed == len(changes.change_set.files) > 0
    assert all(f.added_lines == [] for f in changes.change_set.files)
