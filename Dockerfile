# Reproducible runtime for the three toolchains this service shells out to:
# - Python: Pylint/Radon/Bandit, installed as pinned dependencies via
#   pyproject.toml (see analyzers/python/README.md for exact versions).
# - Node.js: ESLint, installed via tools/eslint/'s own package.json (see
#   analyzers/README.md) — never the analyzed repository's own toolchain,
#   for any language.
# - Java: PMD, a pinned distribution unpacked below. PMD is a Java
#   program, so a headless JRE comes with it; it reads source and never
#   compiles, which is why it is used rather than SpotBugs (bytecode, and
#   therefore a full Maven build with the repository's own plugins).
#
# git is required too: workspace/git_client.py shells out to the real git
# binary to clone/checkout the analyzed repository.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl gnupg unzip default-jre-headless \
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

# PMD, pinned like every other analyzer in this service: its output
# wording is parsed to derive the complexity and size metrics, so a
# version that rephrases a rule would silently empty a dashboard panel.
ARG PMD_VERSION=7.18.0
# mkdir first: this layer runs before `COPY . .`, so tools/pmd/ does not
# exist yet.
RUN mkdir -p /app/tools/pmd \
    && curl -fsSL -o /tmp/pmd.zip \
        "https://github.com/pmd/pmd/releases/download/pmd_releases%2F${PMD_VERSION}/pmd-dist-${PMD_VERSION}-bin.zip" \
    && unzip -q /tmp/pmd.zip -d /tmp/pmd \
    && mv "/tmp/pmd/pmd-bin-${PMD_VERSION}" /app/tools/pmd/pmd-bin \
    && rm -rf /tmp/pmd.zip /tmp/pmd \
    && /app/tools/pmd/pmd-bin/bin/pmd --version

COPY . .
# Re-run install now that the real source is present (editable install
# above only needed pyproject.toml to resolve dependencies).
RUN pip install --no-cache-dir -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "analysis_engine.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
