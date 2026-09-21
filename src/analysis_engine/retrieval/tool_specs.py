import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .tools import RetrievalTools, ToolError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolSpec:
    """
    A tool as the model sees it: name, one-line purpose, JSON schema.

    Provider-neutral. The provider adapter (Phase 2) turns these into the
    vendor's own format. Descriptions are deliberately short: every tool
    definition is sent with *every* model call, so each word here is paid
    for on each round of every review.
    """

    name: str
    description: str
    parameters: dict[str, Any]


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    # OpenAI "strict" function calling: every property listed as required,
    # optional ones typed as nullable, no extra properties. The API then
    # guarantees the arguments match, so a malformed call can't waste a
    # paid round trip.
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_OPTIONAL_STRING = {"type": ["string", "null"]}
_OPTIONAL_INT = {"type": ["integer", "null"]}

TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "read_file",
        "Read numbered lines of a repository file (max 300 per call).",
        _schema({"path": _STRING, "start_line": _OPTIONAL_INT, "end_line": _OPTIONAL_INT}),
    ),
    ToolSpec(
        "get_symbol_source",
        "Full source of a function, method or class, by symbol id.",
        _schema({"symbol_id": _STRING}),
    ),
    ToolSpec(
        "find_symbol",
        "Find functions, methods or classes by name ('total') or qualified name ('Cart.total').",
        _schema({"name": _STRING}),
    ),
    ToolSpec(
        "callers_of",
        "Known call sites of a symbol. Incomplete: calls to ambiguous names aren't linked.",
        _schema({"symbol_id": _STRING}),
    ),
    ToolSpec(
        "callees_of",
        "What a symbol calls, with line numbers.",
        _schema({"symbol_id": _STRING}),
    ),
    ToolSpec(
        "list_tests_for",
        "Test functions that call a symbol (matched by name).",
        _schema({"symbol_id": _STRING}),
    ),
    ToolSpec(
        "search_code",
        "Literal text search across the repository. Optional path_glob like 'src/**/*.py'.",
        _schema({"text": _STRING, "path_glob": _OPTIONAL_STRING}),
    ),
    ToolSpec(
        "get_rule",
        "A business rule's full text, by id (e.g. 'BR-PRICING-001').",
        _schema({"rule_id": _STRING}),
    ),
    ToolSpec(
        "get_linter_findings",
        "Linter findings, optionally filtered by file, rule, or changed lines only.",
        _schema({"path": _OPTIONAL_STRING, "rule_id": _OPTIONAL_STRING, "changed_only": {"type": ["boolean", "null"]}}),
    ),
)


# Argument models: checked again here even though strict mode already
# enforces the schema, because the executor must never trust input that
# came from a model, and tests call it directly.
class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ReadFile(_Args):
    path: str
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class _SymbolId(_Args):
    symbol_id: str


class _Name(_Args):
    name: str = Field(min_length=1, max_length=200)


class _Search(_Args):
    text: str
    path_glob: str | None = Field(default=None, max_length=200)


class _RuleId(_Args):
    rule_id: str = Field(min_length=1, max_length=50)


class _Findings(_Args):
    path: str | None = None
    rule_id: str | None = None
    changed_only: bool | None = None


@dataclass
class ToolCallRecord:
    """One executed tool call, kept for the review's trace (cost and debugging)."""

    name: str
    arguments: dict[str, Any]
    result_chars: int
    error: bool
    duration_ms: int


@dataclass
class ToolExecutor:
    """
    Runs tool calls from a model against one repository.

    Never raises: every problem — unknown tool, bad arguments, a path
    outside the workspace, a bug in a tool — comes back as a short text
    result the model can read and correct. An exception here would end
    the whole review over one bad call.
    """

    tools: RetrievalTools
    calls: list[ToolCallRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        t = self.tools
        self._handlers: dict[str, tuple[type[_Args], Callable[..., str | Awaitable[str]]]] = {
            "read_file": (_ReadFile, t.read_file),
            "get_symbol_source": (_SymbolId, t.get_symbol_source),
            "find_symbol": (_Name, t.find_symbol),
            "callers_of": (_SymbolId, t.callers_of),
            "callees_of": (_SymbolId, t.callees_of),
            "list_tests_for": (_SymbolId, t.list_tests_for),
            "search_code": (_Search, t.search_code),
            "get_linter_findings": (_Findings, t.get_linter_findings),
            "get_rule": (_RuleId, t.get_rule),
        }

    async def execute(self, name: str, arguments: dict[str, Any] | str) -> str:
        started = time.monotonic()
        parsed: dict[str, Any] = {}
        error = True

        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
            handler = self._handlers.get(name)
            if handler is None:
                result = f"Error: unknown tool '{name}'."
            else:
                model, function = handler
                args = model.model_validate(parsed)
                kwargs = {k: v for k, v in args.model_dump().items() if v is not None}
                outcome = function(**kwargs)
                result = await outcome if isinstance(outcome, Awaitable) else outcome
                error = False
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            result = f"Error: invalid arguments for {name}: {_first_line(exc)}"
        except ToolError as exc:
            result = f"Error: {exc}"
        except Exception:
            logger.exception("tool %s failed", name)
            result = f"Error: {name} failed unexpectedly."

        self.calls.append(ToolCallRecord(
            name=name, arguments=parsed, result_chars=len(result), error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
        ))
        return result


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0][:200]
