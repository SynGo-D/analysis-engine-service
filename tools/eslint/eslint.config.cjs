// Fixed, generic ruleset applied to every analyzed repository — the
// repository's own ESLint config/devDependencies are deliberately never
// used. See analyzers/README.md / workspace/README.md for why: installing
// and running a repo-controlled devDependency tree (npm install, which
// can execute arbitrary postinstall scripts) on the analysis host would
// undermine "never execute untrusted repository code directly on the
// host."
const js = require("@eslint/js");
const tseslint = require("typescript-eslint");
const globals = require("globals");

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
  },
  {
    ignores: ["**/node_modules/**", "**/dist/**", "**/build/**"],
  },
];
