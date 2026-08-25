import sys
from pathlib import Path

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

    # Per-tool-invocation timeout for analyzer subprocesses (linters,
    # reviewdog). Analyzing untrusted code should never hang a worker.
    analyzer_timeout_seconds: float = 120.0

    # Analyzer tool locations — resolved to absolute paths against this
    # service's project root at class-definition time. This matters:
    # analyzer subprocesses run with cwd set to the *analyzed workspace*
    # (a temp directory elsewhere), not this project's root, so a plain
    # relative default would resolve against the wrong directory (caught
    # by actually running this against a real workspace — a bare
    # "tools/eslint/node_modules/.bin/eslint" string produced
    # FileNotFoundError once cwd was the workspace, not this project).
    # Still overridable via env vars for production/container deployments
    # where these tools live at standard absolute paths instead (e.g. a
    # globally-installed cppcheck).
    eslint_bin_path: str = str(_PROJECT_ROOT / "tools/eslint/node_modules/.bin/eslint")
    eslint_config_path: str = str(_PROJECT_ROOT / "tools/eslint/eslint.config.cjs")
    reviewdog_bin_path: str = str(_PROJECT_ROOT / ".tools-bin/reviewdog")
    # Installed via the "cppcheck" PyPI package (pyproject.toml) — a
    # manylinux wheel bundling the real compiled binary, verified
    # end-to-end, not just installed and assumed to work. Resolved next to
    # sys.executable (this venv's own bin/ dir) rather than relying on
    # PATH/activation order, same reasoning as the other tool paths above.
    cppcheck_bin_path: str = str(Path(sys.executable).parent / "cppcheck")


settings = Settings()
