from pathlib import Path

from analysis_engine.diffing import ON_CHANGED_LINE, changed_symbols, mark_findings
from analysis_engine.domain import ChangedFile, ChangeSet, Finding
from analysis_engine.indexing import CodeIndexer


def _change_set(*files: ChangedFile) -> ChangeSet:
    return ChangeSet(base_sha="b" * 40, head_sha="h" * 40, target_branch="main", files=list(files))


def _finding(path: str, line: int | None, end_line: int | None = None) -> Finding:
    return Finding(
        repository="acme/shop", pull_request_number=1, commit_sha="h" * 40,
        file_path=path, line=line, end_line=end_line, severity="warning",
        category="code_smell", rule_id="r", message="m", tool="pylint", fingerprint=f"{path}:{line}",
    )


# ---------------------------------------------------------------------------
# mark_findings
# ---------------------------------------------------------------------------


def test_marks_only_findings_on_added_lines():
    change_set = _change_set(ChangedFile(path="a.py", status="modified", added_lines=[5, 6]))
    findings = [_finding("a.py", 5), _finding("a.py", 7), _finding("other.py", 5)]

    marked, count = mark_findings(findings, change_set)

    assert [f.metadata[ON_CHANGED_LINE] for f in marked] == [True, False, False]
    assert count == 1


def test_a_multi_line_finding_counts_if_any_of_its_lines_changed():
    change_set = _change_set(ChangedFile(path="a.py", status="modified", added_lines=[12]))

    marked, _ = mark_findings([_finding("a.py", 10, end_line=14)], change_set)

    assert marked[0].metadata[ON_CHANGED_LINE] is True


def test_a_finding_next_to_a_deletion_is_not_on_a_changed_line():
    # deletion_points find the enclosing function; they are not changes to
    # the line itself.
    change_set = _change_set(ChangedFile(path="a.py", status="modified", deletion_points=[4]))

    marked, _ = mark_findings([_finding("a.py", 4)], change_set)

    assert marked[0].metadata[ON_CHANGED_LINE] is False


def test_a_file_level_finding_counts_only_when_the_files_content_changed():
    change_set = _change_set(
        ChangedFile(path="edited.py", status="modified", added_lines=[1]),
        ChangedFile(path="moved.py", old_path="was.py", status="renamed"),
    )

    marked, _ = mark_findings([_finding("edited.py", None), _finding("moved.py", None)], change_set)

    assert [f.metadata[ON_CHANGED_LINE] for f in marked] == [True, False]


def test_keeps_existing_metadata_and_leaves_the_input_untouched():
    original = _finding("a.py", 1)
    original.metadata["cwe"] = "CWE-89"
    change_set = _change_set(ChangedFile(path="a.py", status="modified", added_lines=[1]))

    marked, _ = mark_findings([original], change_set)

    assert marked[0].metadata == {"cwe": "CWE-89", ON_CHANGED_LINE: True}
    assert ON_CHANGED_LINE not in original.metadata


# ---------------------------------------------------------------------------
# changed_symbols
# ---------------------------------------------------------------------------

_SERVICE = """class Cart:
    TAX = 1.2

    def total(self, price):
        return price * self.TAX

    def empty(self):
        return []


def checkout(cart):
    return cart.total(10)
"""


def _index(tmp_path: Path):
    (tmp_path / "cart.py").write_text(_SERVICE)
    return CodeIndexer().build(tmp_path)


def test_reports_the_innermost_symbol_not_its_enclosing_class(tmp_path):
    change_set = _change_set(ChangedFile(path="cart.py", status="modified", added_lines=[5]))

    symbols = changed_symbols(_index(tmp_path), change_set)

    assert [s.qualified_name for s in symbols] == ["Cart.total"]


def test_reports_the_class_when_its_own_body_changed(tmp_path):
    change_set = _change_set(ChangedFile(path="cart.py", status="modified", added_lines=[2]))

    symbols = changed_symbols(_index(tmp_path), change_set)

    assert [s.qualified_name for s in symbols] == ["Cart"]


def test_maps_a_pure_deletion_to_its_function(tmp_path):
    change_set = _change_set(ChangedFile(path="cart.py", status="modified", deletion_points=[8]))

    assert [s.qualified_name for s in changed_symbols(_index(tmp_path), change_set)] == ["Cart.empty"]


def test_counts_known_callers_as_the_blast_radius(tmp_path):
    change_set = _change_set(ChangedFile(path="cart.py", status="modified", added_lines=[5, 12]))

    symbols = {s.qualified_name: s for s in changed_symbols(_index(tmp_path), change_set)}

    assert symbols["Cart.total"].callers_count == 1   # checkout() calls it
    assert symbols["checkout"].callers_count == 0


def test_ignores_deleted_files_and_lines_outside_any_symbol(tmp_path):
    change_set = _change_set(
        ChangedFile(path="cart.py", status="modified", added_lines=[9]),   # blank line between symbols
        ChangedFile(path="gone.py", status="deleted"),
    )

    assert changed_symbols(_index(tmp_path), change_set) == []
