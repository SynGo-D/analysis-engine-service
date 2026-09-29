"""
An empty OPENAI_API_KEY means "no key", not "a key that is empty".

Without this the service dies at startup with "Missing credentials" when
a deployment simply did not ask for the AI review — which is what an
unset Kubernetes secret value, a blank line in .env and an empty Compose
variable all produce.
"""
import importlib
import sys

import pytest


def _settings(monkeypatch, key: str | None, tmp_path=None):
    # Settings reads a .env beside the working directory, and this
    # repository has a real one. Without moving away from it, "no key set"
    # silently picks up the developer's own.
    if tmp_path is not None:
        monkeypatch.chdir(tmp_path)

    for name, value in {"DB_HOST": "x", "DB_NAME": "x", "DB_USER": "x", "DB_PASSWORD": "x"}.items():
        monkeypatch.setenv(name, value)

    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)

    # Settings is instantiated at import; a fresh read needs a reload.
    import analysis_engine.config as config
    return importlib.reload(config).settings


@pytest.mark.parametrize("key", ["", "   ", "\n"])
def test_a_blank_key_disables_the_review(monkeypatch, key):
    assert _settings(monkeypatch, key).agent_review_available is False


def test_no_key_at_all_disables_the_review(monkeypatch, tmp_path):
    assert _settings(monkeypatch, None, tmp_path).agent_review_available is False


def test_a_real_key_enables_it(monkeypatch):
    assert _settings(monkeypatch, "sk-proj-not-a-real-key").agent_review_available is True


def test_the_switch_still_wins_over_a_real_key(monkeypatch):
    monkeypatch.setenv("AGENT_REVIEW_ENABLED", "false")
    assert _settings(monkeypatch, "sk-proj-not-a-real-key").agent_review_available is False
