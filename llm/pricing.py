"""llm/pricing.py — token pricing per provider/model (USD per 1M tokens)."""
from __future__ import annotations

# (provider, model_prefix) -> (input_per_1m, output_per_1m)
_PRICING: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-opus-5"):     (15.00, 75.00),
    ("anthropic", "claude-sonnet-5"):   (3.00,  15.00),
    ("anthropic", "claude-sonnet-4"):   (3.00,  15.00),
    ("anthropic", "claude-haiku"):      (0.80,   4.00),
    ("deepseek",  "deepseek-chat"):     (0.27,   1.10),
    ("deepseek",  "deepseek-reasoner"): (0.55,   2.19),
    ("openai",    "gpt-4o-mini"):       (0.15,   0.60),
    ("openai",    "gpt-4o"):            (2.50,  10.00),
    ("openai",    "o1"):                (15.00, 60.00),
    ("gemini",    "gemini-2.0-flash"):  (0.075,  0.30),
    ("gemini",    "gemini-1.5-pro"):    (1.25,   5.00),
    ("ollama",    ""):                  (0.00,   0.00),
}

_FALLBACK = (1.00, 3.00)  # unknown model


def estimate_tokens(text: str) -> int:
    """Rough chars→tokens estimate for streamed output (providers don't report usage
    in streaming mode). ~4 chars/token is the standard Latin-text heuristic — good
    enough for a cost ledger whose rates are already hardcoded estimates."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated USD cost for one LLM call."""
    provider = (provider or "").lower()
    model = (model or "").lower()
    rate = _FALLBACK
    best_len = -1
    for (p, m_prefix), prices in _PRICING.items():
        if p == provider and model.startswith(m_prefix) and len(m_prefix) > best_len:
            rate = prices
            best_len = len(m_prefix)
    in_cost = input_tokens * rate[0] / 1_000_000
    out_cost = output_tokens * rate[1] / 1_000_000
    return round(in_cost + out_cost, 8)
