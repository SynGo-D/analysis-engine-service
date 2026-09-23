import asyncio
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from analysis_engine.diffing import ON_CHANGED_LINE
from analysis_engine.domain import Finding
from analysis_engine.indexing import CodeIndexer
from analysis_engine.retrieval import (
    REDACTED, PathNotAllowed, RetrievalTools, ToolExecutor, TOOL_SPECS, finding_ref, redact,
    resolve_finding_ref, resolve_in_workspace,
)
from analysis_engine.retrieval import tools as tools_module

_CART = """class Cart:
    def total(self, price):
        return price * 1.2


def checkout(cart):
    return cart.total(10)
"""

_TEST = """from cart import checkout


def test_checkout():
    assert checkout(None) == 12
"""


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "cart.py").write_text(_CART)
    (repo / "tests/test_cart.py").write_text(_TEST)
    (repo / "long.py").write_text("\n".join(f"x{i} = {i}" for i in range(1, 701)) + "\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "x"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env, capture_output=True)
    return repo


def _finding(path, line, severity="warning", changed=False, rule="r1"):
    return Finding(repository="a/b", pull_request_number=1, commit_sha="h", file_path=path, line=line,
                   severity=severity, category="code_smell", rule_id=rule, message="msg", tool="pylint",
                   fingerprint=hashlib.sha256(f"{path}:{line}:{rule}".encode()).hexdigest(), metadata={ON_CHANGED_LINE: changed})


@pytest.fixture
def tools(tmp_path) -> RetrievalTools:
    repo = _repo(tmp_path)
    findings = [_finding("cart.py", 3, "error", changed=True), _finding("cart.py", 7), _finding("long.py", 1, "info")]
    return RetrievalTools(repo, CodeIndexer().build(repo), findings)


# ---------------------------------------------------------------------------
# Path guard: the model chooses these paths, after reading attacker-written code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "a/../../x", "", ".git/config", ".git"])
def test_refuses_paths_outside_the_repository_or_into_git_internals(tmp_path, path):
    with pytest.raises(PathNotAllowed):
        resolve_in_workspace(_repo(tmp_path), path)


def test_refuses_a_symlink_that_points_outside(tmp_path):
    repo = _repo(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("host secret")
    os.symlink(secret, repo / "innocent.py")

    with pytest.raises(PathNotAllowed):
        resolve_in_workspace(repo, "innocent.py")


def test_read_file_turns_a_refused_path_into_a_readable_error(tools):
    result = asyncio.run(ToolExecutor(tools).execute("read_file", {"path": "../../etc/passwd"}))

    assert result.startswith("Error: Path must be relative")


# ---------------------------------------------------------------------------
# Redaction: whatever reaches a prompt is sent to the model provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("secret", [
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "a" * 36,
    "sk-proj-" + "b" * 40,
    "xoxb-1234567890-abcdefghij",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
])
def test_redacts_known_credential_shapes(secret):
    assert secret not in redact(f"token = {secret}\n")


def test_redacts_the_value_of_a_hard_coded_password_but_keeps_the_code_visible():
    redacted = redact('DB_PASSWORD = "hunter2hunter2"')

    assert redacted == f'DB_PASSWORD = "{REDACTED}"'


def test_redacts_credentials_inside_urls():
    assert redact("https://user:t0ps3cret@github.com/a/b.git") == f"https://user:{REDACTED}@github.com/a/b.git"


def test_leaves_ordinary_code_and_hashes_alone():
    code = 'sha = "3f786850e387550fdab836ed7e6dc881de23001b"\npassword_length = 12\n'
    assert redact(code) == code


def test_redacts_a_private_key_block():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
    assert "MIIEpAIBAAKCAQEA" not in redact(key)


def test_read_file_output_is_redacted(tmp_path):
    repo = _repo(tmp_path)
    (repo / "config.py").write_text('API_KEY = "sk-proj-' + "c" * 40 + '"\n')
    result = RetrievalTools(repo, CodeIndexer().build(repo), []).read_file("config.py")

    assert "c" * 40 not in result
    assert REDACTED in result


# ---------------------------------------------------------------------------
# The tools themselves
# ---------------------------------------------------------------------------


def test_read_file_numbers_lines_and_stops_at_the_limit(tools):
    result = tools.read_file("long.py")

    assert result.startswith("long.py (lines 1-300 of 700)")
    assert "    1| x1 = 1" in result
    assert "x301" not in result
    assert "start_line=301" in result


def test_read_file_clips_very_long_lines(tmp_path):
    repo = _repo(tmp_path)
    (repo / "min.js").write_text("a" * 5_000 + "\n")
    result = RetrievalTools(repo, CodeIndexer().build(repo), []).read_file("min.js")

    assert "[line truncated]" in result
    assert len(result) < 600


def test_find_symbol_and_get_its_source(tools):
    found = tools.find_symbol("Cart.total")
    assert "method Cart.total — cart.py:2-3" in found

    source = tools.get_symbol_source("cart.py::Cart.total")
    assert "    3|         return price * 1.2" in source


def test_callers_of_gives_the_call_site_line(tools):
    assert "checkout calls it at cart.py:7" in tools.callers_of("cart.py::Cart.total")


def test_callers_of_says_an_empty_answer_is_not_proof(tools):
    assert "doesn't prove it's unused" in tools.callers_of("tests/test_cart.py::test_checkout")


def test_list_tests_for_finds_tests_calling_the_function(tools):
    assert "test_checkout — tests/test_cart.py" in tools.list_tests_for("cart.py::checkout")


def test_search_code_is_literal_and_bounded(tools):
    result = asyncio.run(tools.search_code("price * 1.2"))
    assert "cart.py:3:" in result

    # Regex metacharacters are searched for literally, not interpreted.
    assert asyncio.run(tools.search_code("pr.ce")) == "No matches for 'pr.ce'."


def test_search_code_honours_a_path_glob(tools):
    result = asyncio.run(tools.search_code("checkout", path_glob="tests/**"))

    assert "tests/test_cart.py" in result
    assert "cart.py:6" not in result


def test_get_linter_findings_filters_and_orders_by_severity(tools):
    changed = tools.get_linter_findings(changed_only=True)
    assert changed.count("\n") == 0 and "error pylint/r1 cart.py:3" in changed

    everything = tools.get_linter_findings().splitlines()
    assert [line.split()[1] for line in everything] == ["error", "warning", "info"]


def test_results_say_when_something_was_left_out(tools, monkeypatch):
    monkeypatch.setattr(tools_module, "FINDINGS_MAX", 1)
    assert "[2 more not shown" in tools.get_linter_findings()


# ---------------------------------------------------------------------------
# Executor: never raises, whatever the model sends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, arguments, expected", [
    ("delete_everything", {}, "Error: unknown tool"),
    ("read_file", "{not json", "Error: invalid arguments"),
    ("read_file", {"path": "cart.py", "surprise": 1}, "Error: invalid arguments"),
    ("read_file", {"path": "cart.py", "start_line": 0}, "Error: invalid arguments"),
    ("get_symbol_source", {"symbol_id": "nope"}, "Error: Unknown symbol id"),
    ("search_code", {"text": "ab", "path_glob": None}, "Error: Search text must be at least"),
])
def test_bad_calls_come_back_as_text_the_model_can_correct(tools, name, arguments, expected):
    executor = ToolExecutor(tools)

    result = asyncio.run(executor.execute(name, arguments))

    assert result.startswith(expected)
    assert executor.calls[-1].error is True


def test_accepts_json_string_arguments_and_nulls_for_optional_fields(tools):
    executor = ToolExecutor(tools)

    result = asyncio.run(executor.execute("read_file", '{"path": "cart.py", "start_line": null, "end_line": null}'))

    assert result.startswith("cart.py (lines 1-7 of 7)")
    assert executor.calls[-1].error is False
    assert executor.calls[-1].result_chars == len(result)


def test_every_spec_has_a_handler_and_a_strict_schema(tools):
    executor = ToolExecutor(tools)
    for spec in TOOL_SPECS:
        assert spec.name in executor._handlers
        assert spec.parameters["additionalProperties"] is False
        assert set(spec.parameters["required"]) == set(spec.parameters["properties"])


def test_finding_refs_are_short_and_resolve_back():
    findings = [_finding("a.py", 1), _finding("a.py", 2)]
    ref = finding_ref(findings[0])

    assert len(ref) == 10
    assert resolve_finding_ref(ref, findings) is findings[0]
    assert resolve_finding_ref(ref[:5], findings) is None      # too short to trust
    assert resolve_finding_ref("0" * 10, findings) is None      # matches nothing


@pytest.mark.parametrize("path", [".env", "config/.env.production", "deploy/server.pem", "keys/id_rsa",
                                  "secrets.yaml", "gcp/service-account-prod.json", ".npmrc"])
def test_credential_files_are_never_shown_to_an_agent(tools, path):
    result = asyncio.run(ToolExecutor(tools).execute("read_file", {"path": path}))

    assert result.startswith("Error: Files that hold credentials are never shown.")


@pytest.mark.parametrize("path", ["src/environment.py", "docs/keys.md", "app/secret_santa.py", "env/config.py"])
def test_ordinary_files_that_merely_look_similar_are_readable(path):
    from analysis_engine.retrieval import is_secret_file

    assert not is_secret_file(path)


def test_search_never_returns_lines_from_credential_files(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".env").write_text("DB_PASSWORD=hunter2hunter2\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    tools = RetrievalTools(repo, CodeIndexer().build(repo), [])

    assert "hunter2" not in asyncio.run(tools.search_code("DB_PASSWORD"))
