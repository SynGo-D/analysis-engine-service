from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

SymbolKind = Literal["class", "function", "method"]

# "contains" is structural (class → its methods); "calls" and "imports" are
# what make the graph useful for review — they answer "what else does this
# change touch?", which a per-file linter structurally cannot.
EdgeKind = Literal["calls", "imports", "contains"]


class Symbol(BaseModel):
    """
    One named, addressable thing in the codebase — a class, function, or
    method. Deliberately not every AST node: the index exists to answer
    review questions ("who calls this?", "what did this diff touch?"), and
    a node-per-token graph would be enormous without answering them any
    better.
    """

    symbol_id: str = Field(description='Stable identity: "<file_path>::<qualified_name>"')
    name: str
    qualified_name: str = Field(description='Enclosing scope included, e.g. "Greeter.greet"')
    kind: SymbolKind
    file_path: str
    # 1-based and inclusive on both ends, matching how diffs, editors, and
    # Finding.line already talk about line numbers in this service.
    start_line: int
    end_line: int
    language: str

    def contains_line(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line


class Edge(BaseModel):
    """
    A directed relationship between a symbol and something it references.

    `target_symbol_id` is best-effort: it's populated only when the target
    name matches exactly one symbol in the repository. Anything ambiguous
    (two classes with a `save` method, an imported name shadowing a local
    one) is left unresolved rather than guessed — a wrong edge is worse
    than a missing one here, because downstream agents will state it as
    fact. Real resolution needs type inference, which is deliberately out
    of scope for an AST-level index.
    """

    kind: EdgeKind
    source_symbol_id: str
    target_name: str
    target_symbol_id: str | None = None
    file_path: str
    line: int


class RepoIndex(BaseModel):
    """
    The whole-repository map an agent reads instead of guessing.

    Built fresh from a workspace by `indexing/indexer.py`, serializable so
    it can later be cached per (repository, commit) rather than rebuilt on
    every pull request.
    """

    symbols: list[Symbol] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    files_indexed: int = 0
    files_failed: int = 0

    # Lookup tables — derived, not part of the serialized shape, rebuilt on
    # load so a cached index behaves identically to a freshly-built one.
    _by_id: dict[str, Symbol] = PrivateAttr(default_factory=dict)
    _by_name: dict[str, list[Symbol]] = PrivateAttr(default_factory=dict)
    _by_file: dict[str, list[Symbol]] = PrivateAttr(default_factory=dict)
    _outgoing: dict[str, list[Edge]] = PrivateAttr(default_factory=dict)
    _incoming: dict[str, list[Edge]] = PrivateAttr(default_factory=dict)
    _calls_by_target_name: dict[str, list[Edge]] = PrivateAttr(default_factory=dict)

    def model_post_init(self, _context) -> None:
        for symbol in self.symbols:
            self._by_id[symbol.symbol_id] = symbol
            self._by_name.setdefault(symbol.name, []).append(symbol)
            self._by_file.setdefault(symbol.file_path, []).append(symbol)

        for edge in self.edges:
            self._outgoing.setdefault(edge.source_symbol_id, []).append(edge)
            if edge.target_symbol_id:
                self._incoming.setdefault(edge.target_symbol_id, []).append(edge)
            if edge.kind == "calls":
                self._calls_by_target_name.setdefault(edge.target_name, []).append(edge)

    # -------------------------------------------------------------------
    # Queries — the actual interface agents (and Step 2's retrieval tools)
    # use. Everything here is a dictionary lookup, never a scan, so an
    # agent can ask many questions per review without a latency cost.
    # -------------------------------------------------------------------

    def get(self, symbol_id: str) -> Symbol | None:
        return self._by_id.get(symbol_id)

    def find_by_name(self, name: str) -> list[Symbol]:
        return list(self._by_name.get(name, []))

    def symbols_in_file(self, file_path: str) -> list[Symbol]:
        return list(self._by_file.get(file_path, []))

    def symbols_touching_lines(self, file_path: str, lines: set[int]) -> list[Symbol]:
        """
        The bridge from a diff to the graph: given the lines a pull request
        changed in one file, which symbols did it actually modify? Every
        review question downstream starts here.

        Returns the most specific match first (a method before the class
        containing it), so a caller taking `[0]` gets the tightest scope.
        """
        matched = [
            symbol
            for symbol in self._by_file.get(file_path, [])
            if any(symbol.contains_line(line) for line in lines)
        ]
        return sorted(matched, key=lambda s: s.end_line - s.start_line)

    def callees_of(self, symbol_id: str) -> list[Edge]:
        """Outgoing `calls` edges — what this symbol depends on."""
        return [e for e in self._outgoing.get(symbol_id, []) if e.kind == "calls"]

    def callers_of(self, symbol_id: str) -> list[Symbol]:
        """
        Incoming `calls` edges, resolved to their source symbols — the
        blast radius of changing this symbol. Only resolved edges appear
        here (see `Edge.target_symbol_id`).
        """
        callers = [
            self._by_id[e.source_symbol_id]
            for e in self._incoming.get(symbol_id, [])
            if e.kind == "calls" and e.source_symbol_id in self._by_id
        ]
        return list({c.symbol_id: c for c in callers}.values())

    def call_sites_of(self, symbol_id: str) -> list[Edge]:
        """
        Incoming resolved `calls` edges themselves — unlike `callers_of`,
        these keep the line of each call, so a reviewer can be pointed at
        the exact call site rather than just the calling function.
        """
        return [e for e in self._incoming.get(symbol_id, []) if e.kind == "calls"]

    def calls_by_name(self, name: str) -> list[Edge]:
        """
        Every `calls` edge whose callee is spelled `name`, resolved or not.

        Looser than `call_sites_of` on purpose: tests routinely call
        functions whose names are ambiguous repository-wide, so their
        edges never resolve. When the question is "is there a test that
        mentions this?", a name match is the useful signal — the caller
        must treat it as a hint, not a proven link.
        """
        return list(self._calls_by_target_name.get(name, []))

    def imports_of_file(self, file_path: str) -> list[Edge]:
        return [e for e in self.edges if e.kind == "imports" and e.file_path == file_path]

    def neighborhood(self, symbol_id: str) -> dict[str, list[Symbol]]:
        """
        One-hop context pack for a symbol — what Step 2 hands an agent
        instead of the whole repository. Kept to one hop deliberately:
        bounded tool returns are what keep an agent's context (and cost)
        under control.
        """
        callees = [
            self._by_id[e.target_symbol_id]
            for e in self.callees_of(symbol_id)
            if e.target_symbol_id and e.target_symbol_id in self._by_id
        ]
        return {
            "callers": self.callers_of(symbol_id),
            "callees": list({c.symbol_id: c for c in callees}.values()),
        }
