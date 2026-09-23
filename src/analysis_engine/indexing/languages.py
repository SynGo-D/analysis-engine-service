from dataclasses import dataclass, field
from typing import Callable

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node

from ..domain.code_index import SymbolKind


def _is_function_valued_declarator(node: Node) -> bool:
    """
    `const handler = (req) => ...` defines a function; `const MAX = 5`
    doesn't. Both are `variable_declarator` nodes in the JS/TS grammars,
    so the node type alone can't tell them apart — the value child has to
    be inspected.
    """
    value = node.child_by_field_name("value")
    return value is not None and value.type in {"arrow_function", "function_expression"}


@dataclass(frozen=True)
class LanguageSpec:
    """
    Everything the indexer needs to know about one language, as data.

    Adding a language means adding one entry to `_SPECS` below — the
    walker in `indexer.py` never branches on language, the same way
    `AnalyzerFactory` never branches on tool. Node type names come from
    each grammar directly (verified against the installed grammars, not
    assumed from documentation).
    """

    name: str
    extensions: frozenset[str]
    language: Language

    # AST node type -> the kind of Symbol it defines. A "function" nested
    # inside a class is reclassified as a "method" by the indexer, so
    # Python's single `function_definition` covers both cases without the
    # grammar needing a distinct node type for methods.
    definitions: dict[str, SymbolKind]

    call_nodes: frozenset[str]
    import_nodes: frozenset[str]

    # Optional extra test a definition node must pass to count as a symbol.
    predicates: dict[str, Callable[[Node], bool]] = field(default_factory=dict)


_PYTHON = LanguageSpec(
    name="python",
    extensions=frozenset({".py"}),
    language=Language(tree_sitter_python.language()),
    definitions={
        "function_definition": "function",
        "class_definition": "class",
    },
    call_nodes=frozenset({"call"}),
    import_nodes=frozenset({"import_statement", "import_from_statement"}),
)

# JavaScript and TypeScript share a grammar lineage, so they share the
# same node types — only the compiled grammar and file extensions differ.
_JS_TS_DEFINITIONS: dict[str, SymbolKind] = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "method_definition": "method",
    "variable_declarator": "function",
}
_JS_TS_PREDICATES = {"variable_declarator": _is_function_valued_declarator}

_JAVASCRIPT = LanguageSpec(
    name="javascript",
    extensions=frozenset({".js", ".jsx", ".mjs", ".cjs"}),
    language=Language(tree_sitter_javascript.language()),
    definitions=_JS_TS_DEFINITIONS,
    call_nodes=frozenset({"call_expression"}),
    import_nodes=frozenset({"import_statement"}),
    predicates=_JS_TS_PREDICATES,
)

_TYPESCRIPT = LanguageSpec(
    name="typescript",
    extensions=frozenset({".ts"}),
    language=Language(tree_sitter_typescript.language_typescript()),
    definitions=_JS_TS_DEFINITIONS,
    call_nodes=frozenset({"call_expression"}),
    import_nodes=frozenset({"import_statement"}),
    predicates=_JS_TS_PREDICATES,
)

# .tsx needs the TSX grammar specifically — the plain TypeScript grammar
# fails on JSX syntax.
_TSX = LanguageSpec(
    name="typescript",
    extensions=frozenset({".tsx"}),
    language=Language(tree_sitter_typescript.language_tsx()),
    definitions=_JS_TS_DEFINITIONS,
    call_nodes=frozenset({"call_expression"}),
    import_nodes=frozenset({"import_statement"}),
    predicates=_JS_TS_PREDICATES,
)

_SPECS: tuple[LanguageSpec, ...] = (_PYTHON, _JAVASCRIPT, _TYPESCRIPT, _TSX)

_BY_EXTENSION: dict[str, LanguageSpec] = {
    extension: spec for spec in _SPECS for extension in spec.extensions
}


def spec_for_extension(extension: str) -> LanguageSpec | None:
    """None for a file this index doesn't understand — skipped, not an error."""
    return _BY_EXTENSION.get(extension.lower())


def indexable_extensions() -> frozenset[str]:
    return frozenset(_BY_EXTENSION)
