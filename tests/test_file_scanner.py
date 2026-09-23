from pathlib import Path

from analysis_engine.metrics.file_scanner import scan_js_ts_files


def test_scan_js_ts_files_counts_lines_and_ignores_vendored_dirs(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("line1\nline2\nline3\n")
    (tmp_path / "src" / "readme.md").write_text("not js/ts, should be ignored\n")

    ignored_dir = tmp_path / "node_modules" / "some-pkg"
    ignored_dir.mkdir(parents=True)
    (ignored_dir / "index.js").write_text("should\nnot\ncount\n")

    result = scan_js_ts_files(tmp_path)

    assert result == {"src/index.ts": 3}


def test_scan_js_ts_files_returns_empty_map_for_no_js_ts_content(tmp_path: Path):
    (tmp_path / "README.md").write_text("hello\n")

    assert scan_js_ts_files(tmp_path) == {}
