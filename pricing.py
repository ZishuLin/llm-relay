"""Model catalogue and upstream cost estimates.

``input_cost`` / ``output_cost`` are approximate upstream list prices in USD per
million tokens. They are used only to estimate what each request costs upstream,
for monitoring; nothing is charged to users. Update them when providers change
their prices.
"""

MODELS = {
    "gpt-4o": {"provider": "openai", "upstream": "gpt-4o", "input_cost": 2.50, "output_cost": 10.00},
    "gpt-4o-mini": {"provider": "openai", "upstream": "gpt-4o-mini", "input_cost": 0.15, "output_cost": 0.60},
    "claude-sonnet-4-6": {"provider": "anthropic", "upstream": "claude-sonnet-4-6", "input_cost": 3.00, "output_cost": 15.00},
    "claude-haiku-4-5": {"provider": "anthropic", "upstream": "claude-haiku-4-5-20251001", "input_cost": 1.00, "output_cost": 5.00},
    "deepseek-chat": {"provider": "deepseek", "upstream": "deepseek-chat", "input_cost": 0.27, "output_cost": 1.10},
    # Groq models run on Groq's free tier here, so their estimated cost is 0.
    "llama-3.3-70b": {"provider": "groq", "upstream": "llama-3.3-70b-versatile", "input_cost": 0.0, "output_cost": 0.0},
    "llama-3.1-8b": {"provider": "groq", "upstream": "llama-3.1-8b-instant", "input_cost": 0.0, "output_cost": 0.0},
    "mixtral-8x7b": {"provider": "groq", "upstream": "mixtral-8x7b-32768", "input_cost": 0.0, "output_cost": 0.0},
}

UPSTREAM_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",  # Anthropic's OpenAI-compatible endpoint
    "deepseek": "https://api.deepseek.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated upstream cost of one request in USD (0.0 for unknown models)."""
    cfg = MODELS.get(model)
    if not cfg:
        return 0.0
    return input_tokens / 1e6 * cfg["input_cost"] + output_tokens / 1e6 * cfg["output_cost"]
