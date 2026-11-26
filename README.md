# LLM Relay

A multi-provider LLM gateway with an OpenAI-compatible API. One endpoint routes requests to Groq, DeepSeek, OpenAI and Anthropic, with user accounts, admin-granted token quotas, per-request usage tracking and an admin dashboard.

Built with FastAPI, async SQLAlchemy (SQLite) and httpx.

## Features

- **Multiple providers:** Groq, DeepSeek, OpenAI and Anthropic Claude behind one API
- **OpenAI-compatible:** existing clients work by changing only `base_url`
- **Streaming** and non-streaming chat completions
- **Upstream key rotation:** round-robins each provider's API keys and cools a key down automatically when it hits a rate limit
- **User accounts:** sign-up and login with an image CAPTCHA, automatic API key generation
- **Admin-granted token quotas:** each key has a token quota set by an admin; requests stop when it runs out
- **Usage tracking:** every request is logged with its token counts, latency and an estimated upstream cost, for monitoring
- **Admin dashboard:** usage overview, key and quota management, upstream pool status and an API test console

## Project structure

```
llm-relay/
├── main.py              # FastAPI entry point
├── settings.py          # Settings from env vars / config.py
├── config.example.py    # Config template (copy to config.py)
├── models.py            # Database models (User, APIKey, UsageLog)
├── pricing.py           # Model catalogue and upstream cost estimates
├── key_pool.py          # Upstream key rotation with rate-limit cooldowns
├── auth.py              # API key, password and admin authentication
├── router.py            # OpenAI-compatible proxy (streaming supported)
├── admin.py             # Admin endpoints
├── user_api.py          # User sign-up / login endpoints
├── captcha.py           # Image CAPTCHA
├── admin.html           # Admin dashboard page
├── user.html            # User portal page
├── tests/               # pytest suite (upstream calls are mocked)
└── requirements.txt
```

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.example.py config.py
```

Then edit `config.py`:

```python
OPENAI_API_KEYS     = "sk-..."       # comma-separate several keys to rotate them
ANTHROPIC_API_KEYS  = "sk-ant-..."
DEEPSEEK_API_KEYS   = "sk-..."
GROQ_API_KEYS       = "gsk_..."
ADMIN_TOKEN         = "change-me"    # admin password
PROXY_URL           = ""             # optional outbound proxy for upstream calls
DEFAULT_TOKEN_QUOTA = 0              # tokens for new sign-ups (0 = an admin grants them)
PORT                = 8000
```

`config.py` is gitignored, so your keys stay local. Every setting can also be given as an environment variable of the same name (handy for containers). Set `RELAY_DATABASE_URL` to use a database other than `relay.db`.

### 3. Run

```bash
python main.py
```

### 4. Open

| URL | Purpose |
|-----|---------|
| `http://localhost:8000/user` | User sign-up, login, API key and remaining quota |
| `http://localhost:8000/dashboard` | Admin dashboard |
| `http://localhost:8000/docs` | API docs (Swagger) |
| `http://localhost:8000/v1` | OpenAI-compatible API base URL |

## Client usage

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-relay-your-key",
    base_url="http://your-server:8000/v1",
)

response = client.chat.completions.create(
    model="llama-3.3-70b",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)
```

## Supported models

| Model | Provider |
|-------|----------|
| llama-3.3-70b | Groq |
| llama-3.1-8b | Groq |
| mixtral-8x7b | Groq |
| deepseek-chat | DeepSeek |
| gpt-4o-mini | OpenAI |
| gpt-4o | OpenAI |
| claude-sonnet-4-6 | Anthropic |
| claude-haiku-4-5 | Anthropic |

Models are defined in `pricing.py`, together with approximate upstream list prices that are used only to estimate cost for monitoring. Update them when providers change their prices.

## Admin API

Every admin endpoint needs the header `x-admin-token: <ADMIN_TOKEN>`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/keys` | All keys with owner, quota and tokens used |
| POST | `/admin/keys` | Create a key: `{"name": "demo", "token_quota": 100000}` |
| POST | `/admin/keys/{id}/quota` | Set or add to a quota: `{"token_quota": 50000, "mode": "set"}` (`mode` is `set` or `add`) |
| POST | `/admin/keys/{id}/active` | Enable or disable a key: `{"active": false}` |
| GET | `/admin/stats?days=7` | Requests, tokens and estimated upstream cost, by model |
| GET | `/admin/pool` | Upstream keys available per provider |

```bash
# Create a key with a 100k-token quota
curl -X POST http://localhost:8000/admin/keys \
  -H "x-admin-token: change-me" \
  -H "Content-Type: application/json" \
  -d '{"name":"demo","token_quota":100000}'

# Add 50k tokens to key 1's quota
curl -X POST http://localhost:8000/admin/keys/1/quota \
  -H "x-admin-token: change-me" \
  -H "Content-Type: application/json" \
  -d '{"token_quota":50000,"mode":"add"}'

# Usage for the last 7 days
curl "http://localhost:8000/admin/stats?days=7" -H "x-admin-token: change-me"
```

## How it works

- **Key rotation:** `key_pool.py` round-robins each provider's keys. When an upstream answers HTTP 429, that key cools down for `RATE_LIMIT_COOLDOWN` seconds and the request is retried on the next key. If every key is cooling down, the gateway returns 503.
- **Quotas:** a request is refused with 429 once a key's `tokens_used` reaches its `token_quota`. Tokens come from the upstream's `usage` block. For streams the gateway asks for a final usage chunk (`stream_options.include_usage`) and falls back to an estimate when none arrives (logged as `usage_estimated`).
- **Pass-through:** OpenAI parameters the gateway doesn't use itself (`temperature`, `top_p`, `tools`, ...) are forwarded unchanged.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite mocks the upstream providers, so it needs no API keys or network. It covers key rotation and cooldowns, quota enforcement, usage tracking (streaming and non-streaming), the CAPTCHA, and sign-up and login.

## Deployment (systemd)

```bash
cat > /etc/systemd/system/relay.service << EOF
[Unit]
Description=LLM Relay
After=network.target

[Service]
WorkingDirectory=/opt/llm-relay
ExecStart=/opt/llm-relay/venv/bin/python3 main.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl enable relay
systemctl start relay
```

## Notes

- `config.py` and the database file `relay.db` are gitignored.
- Passwords are stored as salted PBKDF2 hashes; user sessions use random tokens.
- CAPTCHA codes are kept in memory, so run a single worker (or move them to Redis).
- Use HTTPS and a domain name in production.
- Groq's free tier has daily limits; add paid keys for heavier traffic.
