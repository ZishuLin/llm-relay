# Copy this file to config.py and fill in your own values.
# config.py is gitignored; never commit real keys.
# Every setting can also be given as an environment variable of the same name.

# Upstream provider API keys. Separate several keys with commas to enable
# rotation; leave a provider empty ("") if you don't use it.
OPENAI_API_KEYS    = ""
ANTHROPIC_API_KEYS = ""
DEEPSEEK_API_KEYS  = ""
GROQ_API_KEYS      = ""

# Password for the admin dashboard and /admin endpoints (x-admin-token header).
# The admin API stays locked while this is empty.
ADMIN_TOKEN = "change-me"

# Optional outbound HTTP(S) proxy for upstream requests; leave empty to disable.
PROXY_URL = ""

# Tokens granted to the key created at sign-up (0 = an admin must grant a quota).
DEFAULT_TOKEN_QUOTA = 0

# Seconds an upstream key rests after the provider answers HTTP 429.
RATE_LIMIT_COOLDOWN = 60

PORT = 8000
