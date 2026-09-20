from pathlib import Path

from analysis_engine.domain.code_index import RepoIndex
from analysis_engine.indexing import CodeIndexer


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------


def test_extracts_python_functions_classes_and_methods(tmp_path):
    _write(tmp_path, "svc.py", """
class Greeter:
    def greet(self, name):
        return helper(name)

def helper(x):
    return x
""")

    index = CodeIndexer().build(tmp_path)
    by_qualified = {s.qualified_name: s for s in index.symbols}

    assert by_qualified["Greeter"].kind == "class"
    # A function nested in a class is reclassified as a method, even though
    # Python's grammar uses one node type for both.
    assert by_qualified["Greeter.greet"].kind == "method"
    assert by_qualified["helper"].kind == "function"
    assert by_qualified["helper"].language == "python"


def test_extracts_javascript_declarations_and_arrow_functions(tmp_path):
    _write(tmp_path, "util.js", """
export class Svc {
  handle(req) { return validate(req); }
}
export function validate(r) { return r; }
const shout = (s) => s.toUpperCase();
const MAX_RETRIES = 5;
""")

    index = CodeIndexer().build(tmp_path)
    names = {s.name for s in index.symbols}

    assert {"Svc", "handle", "validate", "shout"} <= names
    # A const holding a value, not a function, is not a symbol — both are
    # `variable_declarator` nodes, so only the value-type predicate
    # separates them.
    assert "MAX_RETRIES" not in names


def test_extracts_typescript_and_tsx(tmp_path):
    _write(tmp_path, "a.ts", "export function fromTs(x: number): number { return x; }\n")
    _write(tmp_path, "b.tsx", "export function Widget() { return <div>hi</div>; }\n")

    index = CodeIndexer().build(tmp_path)
    names = {s.name for s in index.symbols}

    # .tsx needs the TSX grammar specifically — the plain TypeScript
    # grammar cannot parse JSX.
    assert {"fromTs", "Widget"} <= names
    assert index.files_failed == 0


def test_line_spans_are_one_based_and_inclusive(tmp_path):
    _write(tmp_path, "m.py", "def first():\n    return 1\n")

    symbol = CodeIndexer().build(tmp_path).find_by_name("first")[0]

    assert symbol.start_line == 1
    assert symbol.end_line == 2


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def test_call_edges_are_attributed_to_the_enclosing_symbol(tmp_path):
    _write(tmp_path, "m.py", """
def helper(x):
    return x

def caller(x):
    return helper(x)
""")

    index = CodeIndexer().build(tmp_path)
    callees = index.callees_of("m.py::caller")

    assert [e.target_name for e in callees] == ["helper"]
    assert callees[0].target_symbol_id == "m.py::helper"


def test_method_calls_resolve_to_the_method_name_not_the_receiver(tmp_path):
    _write(tmp_path, "m.py", """
def run(client):
    return client.fetch_data()
""")

    index = CodeIndexer().build(tmp_path)

    assert [e.target_name for e in index.callees_of("m.py::run")] == ["fetch_data"]


def test_callers_of_gives_the_blast_radius_of_a_change(tmp_path):
    _write(tmp_path, "core.py", "def shared():\n    return 1\n")
    _write(tmp_path, "a.py", "from core import shared\ndef a():\n    return shared()\n")
    _write(tmp_path, "b.py", "from core import shared\ndef b():\n    return shared()\n")

    index = CodeIndexer().build(tmp_path)
    callers = {c.qualified_name for c in index.callers_of("core.py::shared")}

    assert callers == {"a", "b"}


def test_ambiguous_names_are_left_unresolved_rather_than_guessed(tmp_path):
    _write(tmp_path, "one.py", "class A:\n    def save(self):\n        return 1\n")
    _write(tmp_path, "two.py", "class B:\n    def save(self):\n        return 2\n")
    _write(tmp_path, "call.py", "def go(x):\n    return x.save()\n")

    index = CodeIndexer().build(tmp_path)
    edge = index.callees_of("call.py::go")[0]

    # Two different `save` methods exist; picking one would be a fabricated
    # fact an agent would later state to a developer as true.
    assert edge.target_name == "save"
    assert edge.target_symbol_id is None


def test_import_edges_capture_the_module(tmp_path):
    _write(tmp_path, "m.py", "import os\nfrom a.b import c\n")
    _write(tmp_path, "m.js", "import { z } from './z';\n")

    index = CodeIndexer().build(tmp_path)

    assert {e.target_name for e in index.imports_of_file("m.py")} == {"os", "a.b"}
    assert {e.target_name for e in index.imports_of_file("m.js")} == {"./z"}


def test_class_contains_edges_link_methods_to_their_class(tmp_path):
    _write(tmp_path, "m.py", "class Svc:\n    def run(self):\n        return 1\n")

    index = CodeIndexer().build(tmp_path)
    contains = [e for e in index.edges if e.kind == "contains"]

    assert contains[0].source_symbol_id == "m.py::Svc"
    assert contains[0].target_symbol_id == "m.py::Svc.run"


# ---------------------------------------------------------------------------
# The diff -> graph bridge
# ---------------------------------------------------------------------------


def test_symbols_touching_lines_returns_most_specific_first(tmp_path):
    _write(tmp_path, "m.py", """
class Svc:
    def run(self):
        value = 1
        return value
""")

    index = CodeIndexer().build(tmp_path)
    touched = index.symbols_touching_lines("m.py", {4})

    # The method, then the class that contains it — a caller taking [0]
    # gets the tightest scope that explains the changed line.
    assert [s.qualified_name for s in touched] == ["Svc.run", "Svc"]


def test_symbols_touching_lines_is_empty_for_untouched_regions(tmp_path):
    _write(tmp_path, "m.py", "def a():\n    return 1\n\n\n\ndef b():\n    return 2\n")

    index = CodeIndexer().build(tmp_path)

    assert index.symbols_touching_lines("m.py", {4}) == []


def test_neighborhood_packs_one_hop_of_context(tmp_path):
    _write(tmp_path, "m.py", """
def leaf():
    return 1

def middle():
    return leaf()

def top():
    return middle()
""")

    index = CodeIndexer().build(tmp_path)
    hood = index.neighborhood("m.py::middle")

    assert [s.qualified_name for s in hood["callers"]] == ["top"]
    assert [s.qualified_name for s in hood["callees"]] == ["leaf"]


# ---------------------------------------------------------------------------
# Robustness — an index that crashes on one bad file is useless in CI
# ---------------------------------------------------------------------------


def test_ignored_directories_are_skipped(tmp_path):
    _write(tmp_path, "src/real.py", "def real():\n    return 1\n")
    _write(tmp_path, "node_modules/pkg/vendored.js", "function vendored() {}\n")
    _write(tmp_path, ".venv/lib/dep.py", "def dep():\n    return 1\n")

    index = CodeIndexer().build(tmp_path)

    assert {s.name for s in index.symbols} == {"real"}
    assert index.files_indexed == 1


def test_unknown_file_types_are_skipped_not_failed(tmp_path):
    _write(tmp_path, "README.md", "# docs\n")
    _write(tmp_path, "data.json", '{"a": 1}\n')

    index = CodeIndexer().build(tmp_path)

    assert index.files_indexed == 0
    assert index.files_failed == 0


def test_syntactically_broken_file_does_not_fail_the_index(tmp_path):
    _write(tmp_path, "broken.py", "def broken(:\n    pass\n")
    _write(tmp_path, "fine.py", "def fine():\n    return 1\n")

    index = CodeIndexer().build(tmp_path)

    # tree-sitter is error-tolerant by design, so a broken file yields
    # whatever it could parse instead of taking the whole run down.
    assert "fine" in {s.name for s in index.symbols}
    assert index.files_indexed == 2


def test_empty_workspace_produces_an_empty_index(tmp_path):
    index = CodeIndexer().build(tmp_path)

    assert index.symbols == []
    assert index.files_indexed == 0


# ---------------------------------------------------------------------------
# Serialization — the index is meant to be cached per commit, not rebuilt
# ---------------------------------------------------------------------------


def test_index_survives_a_serialization_round_trip_with_queries_intact(tmp_path):
    _write(tmp_path, "m.py", "def leaf():\n    return 1\n\ndef top():\n    return leaf()\n")
    original = CodeIndexer().build(tmp_path)

    restored = RepoIndex.model_validate_json(original.model_dump_json())

    # Lookup tables are derived, not serialized — model_post_init has to
    # rebuild them or a cached index would silently answer nothing.
    assert [c.qualified_name for c in restored.callers_of("m.py::leaf")] == ["top"]
    assert restored.find_by_name("top")[0].start_line == 4
