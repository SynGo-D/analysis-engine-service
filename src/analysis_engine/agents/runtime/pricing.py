from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: float
    cached_input: float
    output: float
    # Writing a prompt prefix into the cache. GPT-5.6 and later charge
    # 1.25x input for it; earlier models charge nothing extra (None).
    cache_write: float | None = None

    def cost(self, input_tokens: int, cached_tokens: int, cache_write_tokens: int, output_tokens: int) -> float:
        """
        Cost of one call from the usage the provider reports.

        OpenAI's `input_tokens` is the whole prompt; `cached_tokens` of it
        were read from the cache and `cache_write_tokens` were written to
        it. The rest is billed at the normal input rate. Reasoning tokens
        are already included in `output_tokens`.
        """
        write_rate = self.cache_write if self.cache_write is not None else self.input
        uncached = max(input_tokens - cached_tokens - cache_write_tokens, 0)
        return (
            uncached * self.input
            + cached_tokens * self.cached_input
            + cache_write_tokens * write_rate
            + output_tokens * self.output
        ) / 1_000_000


def _gpt56(input: float, output: float) -> ModelPrice:
    return ModelPrice(input=input, cached_input=input * 0.1, output=output, cache_write=input * 1.25)


# OpenAI standard (short-context) prices from the pricing page and prompt
# caching guide, checked 2026-09-21. Every cost the engine reports is
# computed from this table, so update it when prices change.
OPENAI_PRICES: dict[str, ModelPrice] = {
    "gpt-5.6-luna": _gpt56(input=0.20, output=1.20),
    "gpt-5.6-terra": _gpt56(input=2.00, output=12.00),
    "gpt-5.6-sol": _gpt56(input=4.00, output=20.00),
    "gpt-5.4-nano": ModelPrice(input=0.20, cached_input=0.02, output=1.25),
    "gpt-5.4-mini": ModelPrice(input=0.75, cached_input=0.075, output=4.50),
}


def price_for(model: str) -> ModelPrice | None:
    """The price of a model, or None if it isn't in the table (cost then can't be computed)."""
    return OPENAI_PRICES.get(model)
