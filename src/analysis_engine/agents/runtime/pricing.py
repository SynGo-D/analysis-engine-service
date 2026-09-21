from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: float
    cached_input: float
    output: float

    def cost(self, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float:
        """
        Cost of one call. `input_tokens` is the provider's total prompt
        count, of which `cached_input_tokens` were served from its prompt
        cache at the lower rate — the way OpenAI reports usage.
        """
        uncached = max(input_tokens - cached_input_tokens, 0)
        return (
            uncached * self.input
            + cached_input_tokens * self.cached_input
            + output_tokens * self.output
        ) / 1_000_000


# OpenAI standard (short-context) prices, from the pricing page on
# 2026-09-21. Update here when they change: every cost the engine reports
# is computed from this table.
OPENAI_PRICES: dict[str, ModelPrice] = {
    "gpt-5.6-luna": ModelPrice(input=0.20, cached_input=0.02, output=1.20),
    "gpt-5.4-nano": ModelPrice(input=0.20, cached_input=0.02, output=1.25),
    "gpt-5.4-mini": ModelPrice(input=0.75, cached_input=0.075, output=4.50),
    "gpt-5.6-terra": ModelPrice(input=2.00, cached_input=0.20, output=12.00),
    "gpt-5.6-sol": ModelPrice(input=4.00, cached_input=0.40, output=20.00),
}


def price_for(model: str) -> ModelPrice | None:
    """The price of a model, or None if it isn't in the table (cost then can't be computed)."""
    return OPENAI_PRICES.get(model)
