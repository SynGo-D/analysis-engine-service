import sys
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/analysis_engine/config.py -> src/analysis_engine -> src -> project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """
    Centralised, type-safe access to all environment variables — mirrors
    the Node services' src/config/env.ts pattern so the codebases stay
    consistent to read. Fields with no default are required: pydantic-
    settings raises a ValidationError at import time if they're missing,
    which is this service's fail-fast equivalent of the Node services'
    `process.env.X!` non-null assertions.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    port: int = 8000

    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: str

    rabbitmq_url: str = "amqp://guest:guest@localhost:5672"

    # Origin allowed to read analysis results over CORS — web-interface,
    # same convention as integration-service's FRONTEND_ORIGIN.
    frontend_origin: str = "http://localhost:3000"

    # How many unacked jobs this worker process will hold at once. 1 by
    # default: analysis jobs are long-running (repo clone + linters), so
    # this worker should finish (ack/nack) one before RabbitMQ hands it
    # another — scale by running more worker processes, not by widening
    # this.
    consumer_prefetch_count: int = 1

    # Per-command timeout for git operations against untrusted repository
    # data — bounds a hung/slow clone rather than blocking a worker
    # indefinitely.
    git_clone_timeout_seconds: float = 60.0

    # Per-tool-invocation timeout for the ESLint subprocess. Analyzing
    # untrusted code should never hang a worker.
    analyzer_timeout_seconds: float = 120.0

    # ESLint's location — resolved to an absolute path against this
    # service's project root at class-definition time. This matters: the
    # ESLint subprocess runs with cwd set to the *analyzed workspace* (a
    # temp directory elsewhere), not this project's root, so a plain
    # relative default would resolve against the wrong directory (caught
    # by actually running this against a real workspace — a bare
    # "tools/eslint/node_modules/.bin/eslint" string produced
    # FileNotFoundError once cwd was the workspace, not this project).
    # Still overridable via env vars for production/container deployments
    # where it lives at a standard absolute path instead.
    eslint_bin_path: str = str(_PROJECT_ROOT / "tools/eslint/node_modules/.bin/eslint")
    eslint_config_path: str = str(_PROJECT_ROOT / "tools/eslint/eslint.config.cjs")

    # Per-tool-invocation timeout for the Python analyzers (Pylint, each
    # of Radon's four subcommands, Bandit) — separate from
    # analyzer_timeout_seconds since Python analysis runs three tools
    # concurrently per job rather than ESLint's one, and Radon alone
    # issues four subprocess calls; kept as its own setting so it can be
    # tuned independently without changing ESLint's behavior.
    python_analyzer_timeout_seconds: float = 120.0

    # Pylint/Radon/Bandit are installed as regular Python dependencies
    # (pyproject.toml), unlike ESLint — so, like cppcheck previously, their
    # console-script entry points live next to this venv's own
    # sys.executable rather than under tools/. Still overridable via env
    # vars for container deployments where they're on PATH instead.
    pylint_bin_path: str = str(Path(sys.executable).parent / "pylint")
    radon_bin_path: str = str(Path(sys.executable).parent / "radon")
    bandit_bin_path: str = str(Path(sys.executable).parent / "bandit")

    # -------------------------------------------------------------------
    # Private repositories: clone tokens come from integration-service's
    # internal API. Without INTERNAL_SERVICE_TOKEN, every clone is anonymous
    # (public repositories only).
    # -------------------------------------------------------------------
    integration_service_url: str = "http://localhost:5001"
    internal_service_token: SecretStr | None = None

    # -------------------------------------------------------------------
    # AI review (docs/agent-architecture.md). Without an API key the review
    # stage is skipped and linter analysis runs exactly as before.
    # -------------------------------------------------------------------

    # SecretStr so the key never appears in a repr, a log line or an
    # exception message by accident.
    openai_api_key: SecretStr | None = None
    agent_review_enabled: bool = True

    # Cheapest model first; upgrade only when the evaluation harness shows
    # it falls short (docs/agent-architecture.md §6.1).
    reviewer_model: str = "gpt-5.6-luna"
    # Reasoning tokens are billed as output (the most expensive kind), so
    # effort is kept low. Empty string = don't send the parameter at all,
    # for models that don't support it.
    reviewer_reasoning_effort: str = "low"

    # Hard limits per review. Whichever is hit first ends the review with
    # what it has so far.
    reviewer_max_rounds: int = 8
    reviewer_max_output_tokens: int = 8_000
    reviewer_max_seconds: float = 90.0
    review_max_cost_usd: float = 0.10

    # Verifier (Agent 2): tries to disprove each issue the Reviewer reports.
    # Cheap model: it answers one narrow question per call.
    verifier_enabled: bool = True
    verifier_model: str = "gpt-5.6-luna"
    verifier_reasoning_effort: str = "low"
    verifier_max_rounds: int = 4
    verifier_max_output_tokens: int = 4_000
    verifier_max_seconds: float = 45.0
    verifier_max_cost_usd: float = 0.02      # per issue
    verifier_concurrency: int = 5

    # Rule Miner (Agent 3): on demand only, never per PR.
    rule_miner_model: str = "gpt-5.6-luna"
    rule_miner_max_rounds: int = 8
    rule_miner_max_output_tokens: int = 8_000
    rule_miner_max_seconds: float = 120.0
    rule_miner_max_cost_usd: float = 0.05

    # PRs above either size are skipped: too big to review well, and the
    # cost would scale with them.
    review_max_files: int = 60
    review_max_changed_lines: int = 3_000

    @property
    def agent_review_available(self) -> bool:
        return self.agent_review_enabled and self.openai_api_key is not None


settings = Settings()
