# metrics/python

Computes the aggregate `PylintMetrics`/`RadonComplexityMetrics`/
`MaintainabilityMetrics`/`HalsteadMetrics`/`BanditMetrics`/`PythonMetrics`
(see `../../domain/python_metrics.py`) from raw findings/entries produced
by `analyzers/python/`. Kept as a separate layer from parsing — the same
"raw findings vs. calculated metrics stay separate" split the JS/TS
`metrics/calculator.py` already uses — so removing Python support means
deleting this directory too, without touching `analyzers/python/`'s own
parsing logic (or vice versa).

## LOC / KLOC — sourced from Radon, never recounted

`calculate_python_loc()` builds `PythonLoc` (physical/logical/comment/
blank LOC + KLOC) entirely from Radon's own `radon raw --json` output
(`RadonRawLocEntry`, one entry per file) — there's no independent LOC
scanner for Python the way `metrics/file_scanner.py` is for JS/TS,
per the explicit instruction to reuse Radon's own LOC data rather than
duplicating it. `kloc = physical_loc / 1000`, used consistently for
every density figure below.

## The two-pass density calculation

Pylint/Radon/Bandit run **concurrently** (see
`analyzers/python/python_analyzer.py`), but their density figures
(`issues_per_kloc`, `complexity_per_kloc`, ...) need the final KLOC,
which only exists once Radon's `raw` output has been parsed — after all
three have already finished. Rather than serializing the three tools
behind Radon (losing real concurrency) or threading a partial LOC guess
through each adapter, each `calculate_*_metrics()` function takes an
optional `kloc` parameter (default `0.0`, giving `0.0` densities): each
adapter calls it once with `kloc=0.0` as part of building its own
`*Run`, then `PythonAnalyzer.analyze()` recomputes the *density-dependent
fields only* — a cheap, pure recalculation over already-collected
findings/entries, not a new subprocess call — once the real `kloc` is
known.

## Thresholds

`HIGH_COMPLEXITY_THRESHOLD` (11, Radon's own rank-C boundary) and
`LOW_MAINTAINABILITY_THRESHOLD` (65.0, the commonly-cited "moderate
maintainability" boundary on the standard 0-100 MI scale — confirmed via
direct testing that Radon's JSON `mi` value is already on that scale, not
its own differently-scaled internal rank) live here as the single source
of truth; `analyzers/python/radon_analyzer.py` imports them rather than
keeping its own copies, so "what counts as high complexity" can never
drift between the Finding it creates and the aggregate count here.
