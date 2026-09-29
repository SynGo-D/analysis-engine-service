// Measurement pass — NOT a review.
//
// The review config reports a function only when it breaches a threshold,
// which makes "average complexity" mean "average among the functions that
// were already too complex". That number is biased upward, and it is
// missing entirely for healthy code — a clean repository shows a dash,
// which reads as a broken analyzer rather than as good news.
//
// This config sets both thresholds to 0, so every function reports its
// measured value and the dashboard can show a real average, maximum and
// distribution across the whole codebase.
//
// Its output never becomes a Finding. Nothing here is a rule violation —
// a function with a complexity of 1 is not a problem, it is a data point.
// See metrics/measurement.py, which parses these messages and discards
// everything else.
const sonarjs = require("eslint-plugin-sonarjs");

module.exports = [
  {
    files: ["**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs", "**/*.ts", "**/*.tsx"],
    plugins: { sonarjs },
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      // Maximum 0 means "report every function", not "allow none".
      complexity: ["warn", 0],
      "sonarjs/cognitive-complexity": ["warn", 0],
    },
  },
  // Mirrors the review config: vendored and generated code is not this
  // repository's, so measuring it would describe someone else's work.
  { ignores: ["**/node_modules/**", "**/dist/**", "**/build/**", "**/coverage/**"] },
];
