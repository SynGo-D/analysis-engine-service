// Fixed, generic ruleset applied to every analyzed repository — the
// repository's own ESLint config/devDependencies are deliberately never
// used. See analyzers/README.md / workspace/README.md for why: installing
// and running a repo-controlled devDependency tree (npm install, which
// can execute arbitrary postinstall scripts) on the analysis host would
// undermine "never execute untrusted repository code directly on the
// host."
//
// This service is a dedicated ESLint (JS/TS) code-quality analyzer, not
// just a lint-error reporter — the extra rules below (complexity,
// sonarjs/cognitive-complexity, max-lines, max-lines-per-function) exist
// specifically because AnalysisMetrics (see ../../src/analysis_engine/
// metrics/) is derived from their violation messages, not recomputed
// independently.
const js = require("@eslint/js");
const tseslint = require("typescript-eslint");
const globals = require("globals");
const sonarjs = require("eslint-plugin-sonarjs");

module.exports = [
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // We don't know whether a given repo targets Node.js or the browser
    // (or both) — include both common globals sets rather than guessing,
    // so ordinary builtins (console, require, window, document, ...)
    // aren't false-positived as "not defined".
    languageOptions: {
      globals: {
        ...globals.node,
        ...globals.browser,
        ...globals.es2021,
      },
    },
    plugins: {
      sonarjs,
    },
    rules: {
      // Cyclomatic complexity — reported per-function, message includes
      // the actual measured value ("... has a complexity of N. Maximum
      // allowed is 10."), which metrics/calculator.py parses out.
      complexity: ["error", 10],

      // Cognitive complexity — a distinct metric from cyclomatic
      // complexity (readability/nesting cost, not branch count). Message
      // format: "Refactor this function to reduce its Cognitive
      // Complexity from N to the 15 allowed."
      "sonarjs/cognitive-complexity": ["error", 15],

      // File- and function-level size. Blank lines/comments excluded so
      // formatting/documentation don't inflate a violation.
      "max-lines": ["error", { max: 500, skipBlankLines: true, skipComments: true }],
      "max-lines-per-function": ["error", { max: 100, skipBlankLines: true, skipComments: true }],

      // Dead code — explicit "error" (not left to each config's default)
      // since this rule drives AnalysisMetrics.unused_code.
      "no-unreachable": "error",
    },
  },
  {
    // Plain JS/JSX: the TypeScript plugin isn't registered for these
    // files (typescript-eslint's own recommended config scopes itself to
    // **/*.ts[x]), so unused-vars checking here has to go through core
    // ESLint's own rule, not @typescript-eslint/no-unused-vars.
    files: ["**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs"],
    rules: {
      "no-unused-vars": "error",
    },
  },
  {
    // TypeScript: core no-unused-vars gives false positives on
    // type-only constructs (interfaces used only as types, etc.), so
    // typescript-eslint's own version replaces it here, matching the
    // scoping typescript-eslint/recommended itself uses.
    files: ["**/*.ts", "**/*.tsx"],
    rules: {
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": "error",
    },
  },
  {
    ignores: ["**/node_modules/**", "**/dist/**", "**/build/**", "**/coverage/**"],
  },
];
