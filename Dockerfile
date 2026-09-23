# Reproducible runtime for both toolchains this service shells out to:
# - Python: Pylint/Radon/Bandit, installed as pinned dependencies via
#   pyproject.toml (see analyzers/python/README.md for exact versions).
# - Node.js: ESLint, installed via tools/eslint/'s own package.json (see
#   analyzers/README.md) — never the analyzed repository's own toolchain,
#   for either language.
#
# git is required too: workspace/git_client.py shells out to the real git
# binary to clone/checkout the analyzed repository.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies (FastAPI, asyncpg, aio-pika, Pylint, Radon, Bandit —
# see pyproject.toml for exact pins) — installed before copying the rest
# of the source so this layer only rebuilds when dependencies change.
#
# The empty package is not decoration: setuptools resolves
# [tool.setuptools.packages.find] where = ["src"] while working out what an
# editable install points at, and with no src/ at all the build fails
# outright ("Getting requirements to build editable did not run
# successfully"). A placeholder is enough to satisfy it; COPY . . below
# replaces it with the real source, which the editable install already
# points to.
COPY pyproject.toml ./
RUN mkdir -p src/analysis_engine \
    && touch src/analysis_engine/__init__.py \
    && pip install --no-cache-dir -e ".[dev]"

# ESLint's own dedicated toolchain (never the analyzed repository's).
COPY tools/eslint/package.json tools/eslint/package-lock.json tools/eslint/
RUN cd tools/eslint && npm ci

COPY . .
# Re-run install now that the real source is present (editable install
# above only needed pyproject.toml to resolve dependencies).
RUN pip install --no-cache-dir -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "analysis_engine.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
