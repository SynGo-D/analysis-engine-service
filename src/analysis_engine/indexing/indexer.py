import logging
from pathlib import Path

from tree_sitter import Node, Parser

from .languages import LanguageSpec, spec_for_extension
from ..domain.code_index import Edge, RepoIndex, Symbol

logger = logging.getLogger(__name__)

# Same set the rest of the service skips (factories/language_detector.py,
# analyzers/python/python_analyzer.py) — a committed dependency tree would
# otherwise dominate the graph with code the repository owner didn't write.
_IGNORED_DIR_NAMES = {
    ".git", "node_modules", "dist", "build", "coverage",
    ".venv", "venv", "env", "__pycache__",
}

# A single file larger than this is almost certainly generated (bundles,
# minified output, vendored blobs). Parsing it costs real time and adds
# nothing a reviewer would act on.
_MAX_FILE_BYTES = 1_000_000

# Field names each grammar uses for the thing being imported. Tried in
# order; the first one present wins.
_IMPORT_NAME_FIELDS = ("module_name", "source", "name")

# Field names for the callee within a call node, by the callee's own node
# type — `foo()` is a bare identifier, `obj.foo()` nests the real name
# under a field that differs between the Python and JS/TS grammars.
_CALLEE_NAME_FIELDS = ("attribute", "property")


class CodeIndexer:
    """
    Builds a `RepoIndex` from a checked-out workspace using tree-sitter.

    Deterministic by design: no model calls, no network, no nondeterminism
    — the graph is a fact about the code, and an agent that has to
    reconstruct it every review would be slower, costlier, and capable of
    inventing edges that don't exist. See README.md for why AST-derived
    beats LLM-extracted here.
    """

    def build(self, workspace_path: Path) -> RepoIndex:
        symbols: list[Symbol] = []
        edges: list[Edge] = []
        files_indexed = 0
        files_failed = 0

        for path in sorted(workspace_path.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _IGNORED_DIR_NAMES for part in path.parts):
                continue

            spec = spec_for_extension(path.suffix)
            if spec is None:
                continue

            try:
                source = path.read_bytes()
            except OSError as error:
                logger.warning("index: could not read %s: %s", path, error)
                files_failed += 1
                continue

            if len(source) > _MAX_FILE_BYTES:
                logger.info("index: skipping %s (%d bytes, likely generated)", path, len(source))
                continue

            relative_path = path.relative_to(workspace_path).as_posix()
            try:
                file_symbols, file_edges = self._index_file(source, relative_path, spec)
            except Exception as error:  # a grammar crash must not fail the whole index
                logger.warning("index: failed to parse %s: %s", relative_path, error)
                files_failed += 1
                continue

            symbols.extend(file_symbols)
            edges.extend(file_edges)
            files_indexed += 1

        self._resolve_edge_targets(symbols, edges)

        logger.info(
            "index: %d symbol(s), %d edge(s) across %d file(s) (%d failed)",
            len(symbols), len(edges), files_indexed, files_failed,
        )
        return RepoIndex(
            symbols=symbols, edges=edges,
            files_indexed=files_indexed, files_failed=files_failed,
        )

    # -------------------------------------------------------------------
    # Per-file extraction
    # -------------------------------------------------------------------

    def _index_file(
        self, source: bytes, file_path: str, spec: LanguageSpec
    ) -> tuple[list[Symbol], list[Edge]]:
        tree = Parser(spec.language).parse(source)

        symbols: list[Symbol] = []
        edges: list[Edge] = []
        # Enclosing definitions, outermost first — gives both the qualified
        # name and the owner of any call found at this depth.
        scope: list[Symbol] = []

        def visit(node: Node) -> None:
            symbol = self._symbol_for(node, file_path, spec, scope)

            if symbol is not None:
                symbols.append(symbol)
                if scope:
                    edges.append(Edge(
                        kind="contains",
                        source_symbol_id=scope[-1].symbol_id,
                        target_name=symbol.name,
                        target_symbol_id=symbol.symbol_id,
                        file_path=file_path,
                        line=symbol.start_line,
                    ))
                scope.append(symbol)

            elif node.type in spec.call_nodes and scope:
                # A call outside any definition (module-level side effect)
                # has no meaningful owner, so it's skipped rather than
                # attributed to the file as a whole.
                callee = self._callee_name(node)
                if callee:
                    edges.append(Edge(
                        kind="calls",
                        source_symbol_id=scope[-1].symbol_id,
                        target_name=callee,
                        file_path=file_path,
                        line=node.start_point[0] + 1,
                    ))

            elif node.type in spec.import_nodes:
                imported = self._import_name(node)
                if imported:
                    edges.append(Edge(
                        kind="imports",
                        source_symbol_id=file_path,
                        target_name=imported,
                        file_path=file_path,
                        line=node.start_point[0] + 1,
                    ))

            for child in node.children:
                visit(child)

            if symbol is not None:
                scope.pop()

        visit(tree.root_node)
        return symbols, edges

    def _symbol_for(
        self, node: Node, file_path: str, spec: LanguageSpec, scope: list[Symbol]
    ) -> Symbol | None:
        kind = spec.definitions.get(node.type)
        if kind is None:
            return None

        predicate = spec.predicates.get(node.type)
        if predicate is not None and not predicate(node):
            return None

        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        name = name_node.text.decode(errors="replace")

        # A function defined inside a class is a method, whatever the
        # grammar calls the node — this is why Python needs no separate
        # method node type.
        if kind == "function" and scope and scope[-1].kind == "class":
            kind = "method"

        qualified_name = ".".join([s.name for s in scope] + [name])
        return Symbol(
            symbol_id=f"{file_path}::{qualified_name}",
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            language=spec.name,
        )

    def _callee_name(self, node: Node) -> str | None:
        """`foo()` -> "foo"; `obj.foo()` / `self.foo()` -> "foo" (the method, not the receiver)."""
        function = node.child_by_field_name("function")
        if function is None:
            return None

        for field_name in _CALLEE_NAME_FIELDS:
            attribute = function.child_by_field_name(field_name)
            if attribute is not None:
                return attribute.text.decode(errors="replace")

        if function.type == "identifier":
            return function.text.decode(errors="replace")

        return None

    def _import_name(self, node: Node) -> str | None:
        for field_name in _IMPORT_NAME_FIELDS:
            target = node.child_by_field_name(field_name)
            if target is not None:
                return target.text.decode(errors="replace").strip("\"'")

        # Python's plain `import os` exposes no named field — the module
        # sits in a bare dotted_name child.
        for child in node.children:
            if child.type in {"dotted_name", "string"}:
                return child.text.decode(errors="replace").strip("\"'")

        return None

    # -------------------------------------------------------------------
    # Cross-file resolution
    # -------------------------------------------------------------------

    def _resolve_edge_targets(self, symbols: list[Symbol], edges: list[Edge]) -> None:
        """
        Links `calls` edges to real symbols by unique name.

        Deliberately conservative: a name defined by two different symbols
        stays unresolved. Properly disambiguating it needs type inference
        and import resolution, and a *wrong* edge is worse than a missing
        one — downstream agents will repeat it to a developer as fact.
        """
        by_name: dict[str, list[Symbol]] = {}
        for symbol in symbols:
            by_name.setdefault(symbol.name, []).append(symbol)

        for edge in edges:
            if edge.kind != "calls":
                continue
            candidates = by_name.get(edge.target_name, [])
            if len(candidates) == 1:
                edge.target_symbol_id = candidates[0].symbol_id
