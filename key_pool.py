import logging
import time
from collections import defaultdict
from typing import Callable

import settings

logger = logging.getLogger(__name__)

PLACEHOLDER_MARKERS = ("xxx", "your-")


def parse_keys(keys_str: str) -> list[str]:
    """Split a comma-separated key list, dropping blanks and template placeholders."""
    return [
        k.strip()
        for k in (keys_str or "").split(",")
        if k.strip() and not any(m in k for m in PLACEHOLDER_MARKERS)
    ]


class KeyPool:
    """Round-robin pool of upstream API keys with per-key rate-limit cooldowns."""

    def __init__(self, keys: dict[str, list[str]] | None = None, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._keys: dict[str, list[str]] = {p: list(k) for p, k in (keys or {}).items() if k}
        self._index: dict[str, int] = defaultdict(int)
        self._cooldowns: dict[str, float] = {}
        for provider, ks in self._keys.items():
            logger.info("Loaded %d key(s) for %s", len(ks), provider)

    @classmethod
    def from_settings(cls) -> "KeyPool":
        return cls({
            "openai": parse_keys(settings.OPENAI_API_KEYS),
            "anthropic": parse_keys(settings.ANTHROPIC_API_KEYS),
            "deepseek": parse_keys(settings.DEEPSEEK_API_KEYS),
            "groq": parse_keys(settings.GROQ_API_KEYS),
        })

    def size(self, provider: str) -> int:
        return len(self._keys.get(provider, []))

    def _cooling(self, key: str, now: float) -> bool:
        until = self._cooldowns.get(key)
        return until is not None and now < until

    def get_key(self, provider: str) -> str | None:
        """Next key for the provider that is not cooling down, or None if all are."""
        keys = self._keys.get(provider, [])
        now = self._clock()
        for _ in range(len(keys)):
            idx = self._index[provider] % len(keys)
            self._index[provider] = idx + 1
            if not self._cooling(keys[idx], now):
                return keys[idx]
        return None

    def mark_rate_limited(self, key: str, seconds: float | None = None):
        seconds = settings.RATE_LIMIT_COOLDOWN if seconds is None else seconds
        self._cooldowns[key] = self._clock() + seconds
        logger.warning("Upstream key ...%s rate limited, cooling down for %ss", key[-4:], seconds)

    def stats(self) -> dict:
        now = self._clock()
        return {
            p: {"total": len(ks), "available": sum(1 for k in ks if not self._cooling(k, now))}
            for p, ks in self._keys.items()
        }


key_pool = KeyPool.from_settings()
