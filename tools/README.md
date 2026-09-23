# tools

This service's own dedicated ESLint toolchain — never the analyzed
repository's own tools/config. See `../src/analysis_engine/analyzers/README.md`
for why.

- `eslint/` — a minimal npm project (`package.json` + `eslint.config.cjs`)
  providing ESLint, typescript-eslint, and `eslint-plugin-sonarjs` (for
  cognitive complexity) with a fixed ruleset. Set up with:
  ```bash
  cd tools/eslint && npm install
  ```
