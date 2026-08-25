# tools

This service's own dedicated analyzer toolchain — never the analyzed
repository's own tools/config. See `analyzers/README.md` for why.

- `eslint/` — a minimal npm project (`package.json` + `eslint.config.cjs`)
  providing ESLint + a fixed ruleset. Set up with:
  ```bash
  cd tools/eslint && npm install
  ```
- Pylint, Radon, and Cppcheck are installed via `pyproject.toml` (`pip
  install -e ".[dev]"` at the project root already covers them —
  Cppcheck's PyPI package bundles the real compiled binary, not a
  reimplementation).
- Reviewdog itself is built from source, from the sibling `../reviewdog/`
  clone (the real official `reviewdog/reviewdog` repo) — a local Go
  toolchain lives at `../.dev-toolchains/go` (no sudo, extracted from the
  official tarball) since none was present system-wide. Rebuild with:
  ```bash
  export PATH=../.dev-toolchains/go/bin:$PATH
  cd ../reviewdog && go build -o ../analysis-engine/.tools-bin/reviewdog ./cmd/reviewdog
  ```
  Verified against the same ESLint fixture used in Phase 6 to confirm no
  behavioral difference from the prebuilt release binary originally used
  to develop the analyzer integrations.
  A prebuilt-release install remains a fallback if you'd rather not build
  from source:
  ```bash
  curl -sfL https://raw.githubusercontent.com/reviewdog/reviewdog/master/install.sh \
    | sh -s -- -b .tools-bin
  ```
