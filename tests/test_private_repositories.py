import asyncio
import base64
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

from analysis_engine.domain import AnalysisJob
from analysis_engine.workspace import RepositoryCredentials, WorkspaceManager, WorkspaceSecurityError, git_auth_env, run_git_output
from analysis_engine.workspace import workspace_manager as workspace_module

TOKEN = "gho_private_test_token_0123456789abcdef"


class _Recorder(BaseHTTPRequestHandler):
    """A fake git host: records each request's Authorization header, then says 'not found'."""

    seen: list[str | None] = []

    def do_GET(self):  # noqa: N802
        _Recorder.seen.append(self.headers.get("Authorization"))
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def git_host():
    _Recorder.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


def _checkout(tmp_path: Path, remote: str) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", remote], cwd=tmp_path, check=True)
    return tmp_path


def _fetch(checkout: Path, env: dict[str, str]):
    return asyncio.run(run_git_output(["fetch", "origin", "--", "main"], checkout, 10, env=env))


def test_git_sends_the_token_to_the_repositorys_host(tmp_path, git_host):
    checkout = _checkout(tmp_path, f"{git_host}acme/private.git")

    with pytest.raises(WorkspaceSecurityError):   # the fake host has no repository
        _fetch(checkout, git_auth_env("github", TOKEN, base_url=git_host))

    expected = "Basic " + base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert _Recorder.seen and _Recorder.seen[0] == expected


def test_gitlab_uses_the_oauth2_username(git_host):
    value = git_auth_env("gitlab", TOKEN, base_url=git_host)["GIT_CONFIG_VALUE_0"]

    assert base64.b64decode(value.removeprefix("Authorization: Basic ")).decode() == f"oauth2:{TOKEN}"


def test_the_token_goes_to_no_other_host(tmp_path, git_host):
    # Same server, reached under a different host name: git's extraheader
    # is scoped by URL, so the token must not be sent.
    other = git_host.replace("127.0.0.1", "localhost")
    checkout = _checkout(tmp_path, f"{other}acme/private.git")

    with pytest.raises(WorkspaceSecurityError):
        _fetch(checkout, git_auth_env("github", TOKEN, base_url=git_host))

    assert _Recorder.seen and all(header is None for header in _Recorder.seen)


def test_the_token_is_never_written_to_the_checkout_or_an_error(tmp_path, git_host):
    checkout = _checkout(tmp_path, f"{git_host}acme/private.git")

    with pytest.raises(WorkspaceSecurityError) as error:
        _fetch(checkout, git_auth_env("github", TOKEN, base_url=git_host))

    on_disk = b"".join(p.read_bytes() for p in (checkout / ".git").rglob("*") if p.is_file())
    assert TOKEN.encode() not in on_disk
    assert base64.b64encode(f"x-access-token:{TOKEN}".encode()) not in on_disk
    assert TOKEN not in str(error.value)


def test_credentials_never_leak_into_other_subprocesses():
    # Only the git command gets them; the engine's own environment (which
    # linters and agent tools inherit) never does.
    git_auth_env("github", TOKEN)
    assert all(TOKEN not in value for value in os.environ.values())


# ---------------------------------------------------------------------------
# Asking integration-service for the token
# ---------------------------------------------------------------------------


def _credentials(handler, internal_token="internal-token"):
    return RepositoryCredentials("http://integration.test", internal_token, transport=httpx.MockTransport(handler))


def test_asks_integration_service_with_the_internal_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["query"] = dict(request.url.params)
        return httpx.Response(200, json={"success": True, "data": {"token": TOKEN, "expires_at": None}})

    token = asyncio.run(_credentials(handler).token_for("github", "acme/private"))

    assert token == TOKEN
    assert seen == {"auth": "Bearer internal-token", "query": {"provider": "github", "repository": "acme/private"}}


@pytest.mark.parametrize("response", [
    httpx.Response(404, json={"success": False}),                   # not connected: public clone
    httpx.Response(500, json={"success": False}),
    httpx.Response(200, json={"success": True, "data": {}}),      # malformed
])
def test_anything_but_a_token_means_cloning_anonymously(response):
    assert asyncio.run(_credentials(lambda request: response).token_for("github", "acme/shop")) is None


def test_an_unreachable_integration_service_means_cloning_anonymously():
    def handler(request):
        raise httpx.ConnectError("refused")

    assert asyncio.run(_credentials(handler).token_for("github", "acme/shop")) is None


def test_no_internal_token_means_no_request_at_all():
    def handler(request):
        raise AssertionError("must not be called")

    assert asyncio.run(_credentials(handler, internal_token="").token_for("github", "acme/shop")) is None


# ---------------------------------------------------------------------------
# The workspace carries the credentials to every git call on the checkout
# ---------------------------------------------------------------------------


class _StaticCredentials:
    async def token_for(self, provider, repository):
        return TOKEN


def test_the_workspace_clones_with_the_token_and_keeps_it_for_later_fetches(tmp_path, monkeypatch):
    captured = {}

    async def fake_clone(clone_url, commit_sha, branch, destination, git_env=None, **kwargs):
        captured["env"] = git_env

    monkeypatch.setattr(workspace_module, "clone_commit", fake_clone)
    job = AnalysisJob(provider="github", repository="acme/private", clone_url="https://github.com/acme/private.git",
                      commit_sha="a" * 40, branch="main", pull_request_number=1, queued_at="now")

    async def run():
        async with WorkspaceManager(base_dir=tmp_path, credentials=_StaticCredentials()).prepare(job) as workspace:
            return workspace.git_env, repr(workspace)

    git_env, text = asyncio.run(run())

    assert captured["env"] == git_env == git_auth_env("github", TOKEN)
    assert git_env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert TOKEN not in text   # repr never shows credentials
