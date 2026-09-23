from typing import Any

from pydantic import BaseModel

# Keywords OpenAI's strict mode accepts, per its structured-outputs guide.
# Anything else (defaults, string lengths, formats) is removed from what
# the API sees. Those limits are still enforced by the Pydantic model when
# the answer is validated, so nothing is lost; the API just can't do it
# for us.
_KEEP = {
    "type", "properties", "required", "additionalProperties", "items", "enum",
    "anyOf", "$ref", "$defs", "description", "minItems", "maxItems",
    "minimum", "maximum", "pattern", "const",
}


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """
    A Pydantic model's JSON schema, rewritten for OpenAI strict mode:
    every object lists all its properties as required and forbids extra
    ones. Optional fields keep their `anyOf [..., null]` form, which is
    how strict mode expresses "may be empty".
    """
    return _rewrite(model.model_json_schema())


def _rewrite(node: Any) -> Any:
    if isinstance(node, list):
        return [_rewrite(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _KEEP:
            continue
        if key in ("properties", "$defs"):
            # Maps of *names* to schemas: the names are user-chosen and
            # must not be filtered as if they were schema keywords.
            cleaned[key] = {name: _rewrite(schema) for name, schema in value.items()}
        else:
            cleaned[key] = _rewrite(value)

    if cleaned.get("type") == "object" or "properties" in cleaned:
        cleaned["required"] = list(cleaned.get("properties", {}))
        cleaned["additionalProperties"] = False
    return cleaned
