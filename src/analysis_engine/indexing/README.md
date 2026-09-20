# indexing

Builds a whole-repository symbol/call graph (`domain/code_index.py`'s
`RepoIndex`) from a checked-out workspace, using tree-sitter.

This is the context layer the rest of the review pipeline reads from.
Without it, every analyzer — deterministic or model-driven — sees one
file at a time: ESLint can tell you `foo` is unused in `service.ts`, but
nothing in the service can tell you `foo` was the only caller of a
function this pull request just deleted.

## Why AST-derived, not LLM-extracted

Two options exist for building a code graph: parse it deterministically,
or ask a model to read the repository and describe it. Published
comparisons of the two find deterministic AST-derived graphs give more
reliable multi-hop grounding at substantially lower indexing cost, while
LLM-extracted graphs take far longer to build and can assert
relationships that don't exist.

Measured here, on this service's own source: **50 files → 159 symbols,
937 edges, in 0.03s**, with no tokens spent and no run-to-run variation.
A model-built equivalent would cost money on every pull request and could
invent an edge that a downstream agent then reports to a developer as
fact.

So: this layer stays deterministic, fully unit-tested, and free. Agents
*query* it; they never rebuild it.

## What's in the graph

| | |
|---|---|
| **Symbols** | classes, functions, methods — name, qualified name, file, 1-based inclusive line span, language |
| **`calls` edges** | caller symbol → callee name, resolved to a symbol when unambiguous |
| **`imports` edges** | file → imported module |
| **`contains` edges** | class → its methods |

Not every AST node is a symbol. The index exists to answer review
questions ("who calls this?", "what did this diff touch?"), and a
node-per-token graph would be enormous without answering them any better.

## The query that matters most

`symbols_touching_lines(file_path, lines)` is the bridge from a diff to
the graph — it turns "this PR changed lines 95-100 of pylint_analyzer.py"
into "this PR changed `PylintAnalyzer.run`", which is the starting point
for every question worth asking. It returns the most specific match
first (the method before the class containing it).

From there: `callers_of()` gives the blast radius, `callees_of()` gives
dependencies, and `neighborhood()` packs one hop of both — deliberately
one hop, because bounded tool returns are what keep an agent's context
window and cost under control.

## Deliberate limitations

**Call resolution is by unique name, and conservative.** An edge is
linked to a target symbol only when exactly one symbol in the repository
has that name. Everything else stays unresolved.

On this service's own source that resolves 32% of call edges — and the
unresolved remainder is almost entirely correct: `len`, `sum`, `append`,
`Field`, `Path` are stdlib and third-party calls with no symbol in the
repo to point at. The interesting case is `_relative_path`, which *is*
defined in the repo — three times, in three different analyzer classes.
It stays unresolved rather than picking one at random.

Properly disambiguating that needs type inference and import resolution,
which is out of scope for an AST-level index. A missing edge makes an
agent say less; a wrong edge makes it say something false.

**Skipped:** `.git`, `node_modules`, `dist`, `build`, `coverage`,
`.venv`, `venv`, `env`, `__pycache__` (matching
`factories/language_detector.py`), plus any single file over 1 MB, which
is almost always generated or vendored.

**Error handling:** an unreadable or ungrammatical file increments
`files_failed` and is skipped — one bad file never fails the index.
tree-sitter is error-tolerant, so a file with a syntax error still yields
whatever parsed cleanly.

## Adding a language

Add one `LanguageSpec` entry to `languages.py`: extensions, the compiled
grammar, which node types define symbols, and which are calls/imports.
The walker in `indexer.py` never branches on language — the same
data-driven approach `AnalyzerFactory` uses for tools.

Currently indexed: Python, JavaScript, TypeScript, TSX.
