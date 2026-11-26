"""Runtime settings.

Values come from environment variables first, then from a local ``config.py``
(copy ``config.example.py``), then from the defaults below.
"""

import os

try:
    import config as _config
except ImportError:  # no config.py: rely on env vars and defaults
    _config = None


def _get(name: str, default):
    env = os.environ.get(name)
    if env is not None:
        return type(default)(env) if not isinstance(default, str) else env
    return getattr(_config, name, default)


OPENAI_API_KEYS = _get("OPENAI_API_KEYS", "")
ANTHROPIC_API_KEYS = _get("ANTHROPIC_API_KEYS", "")
DEEPSEEK_API_KEYS = _get("DEEPSEEK_API_KEYS", "")
GROQ_API_KEYS = _get("GROQ_API_KEYS", "")

ADMIN_TOKEN = _get("ADMIN_TOKEN", "")
PROXY_URL = _get("PROXY_URL", "")
PORT = _get("PORT", 8000)

# Tokens granted to a key created at sign-up. 0 means an admin must grant a quota first.
DEFAULT_TOKEN_QUOTA = _get("DEFAULT_TOKEN_QUOTA", 0)

# Seconds an upstream key rests after the provider returns HTTP 429.
RATE_LIMIT_COOLDOWN = _get("RATE_LIMIT_COOLDOWN", 60)

DATABASE_URL = os.environ.get("RELAY_DATABASE_URL", "sqlite+aiosqlite:///relay.db")
