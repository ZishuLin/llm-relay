import os
import sys
import tempfile
from pathlib import Path

import pytest

# Settings are read at import time, so configure a throwaway environment first.
# Environment variables take precedence over any local config.py.
_DB = Path(tempfile.mkdtemp()) / "test-relay.db"
os.environ.update({
    "RELAY_DATABASE_URL": f"sqlite+aiosqlite:///{_DB.as_posix()}",
    "ADMIN_TOKEN": "test-admin",
    "OPENAI_API_KEYS": "",
    "ANTHROPIC_API_KEYS": "",
    "DEEPSEEK_API_KEYS": "sk-test-deepseek-1,sk-test-deepseek-2",
    "GROQ_API_KEYS": "gsk_test_groq_1",
    "PROXY_URL": "",
    "DEFAULT_TOKEN_QUOTA": "0",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import admin  # noqa: E402
import captcha  # noqa: E402
import router  # noqa: E402
from key_pool import KeyPool  # noqa: E402
from main import app  # noqa: E402

ADMIN = {"x-admin-token": "test-admin"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pool(monkeypatch):
    """A fresh upstream key pool for each test, so cooldowns never leak between tests."""
    p = KeyPool({"deepseek": ["sk-test-deepseek-1", "sk-test-deepseek-2"], "groq": ["gsk_test_groq_1"]})
    monkeypatch.setattr(router, "key_pool", p)
    monkeypatch.setattr(admin, "key_pool", p)
    return p


@pytest.fixture
def upstream(monkeypatch):
    """Replace the upstream HTTP client with a mock transport.

    Set ``upstream.handler`` to a function taking an httpx.Request and returning
    an httpx.Response. Every request is recorded in ``upstream.requests``.
    """

    class Upstream:
        handler = None
        requests: list[httpx.Request] = []

    up = Upstream()
    up.requests = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        up.requests.append(request)
        return up.handler(request)

    monkeypatch.setattr(router, "get_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport_handler)))
    return up


def solve_captcha(client: TestClient) -> tuple[str, str]:
    resp = client.get("/captcha/image")
    assert resp.status_code == 200
    token = resp.headers["X-Captcha-Token"]
    return token, captcha._store[token][0]


def register(client: TestClient, email: str, password: str = "s3cret-pass") -> dict:
    token, code = solve_captcha(client)
    resp = client.post("/user/register", json={
        "email": email, "password": password, "captcha_token": token, "captcha_code": code,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def key_id_for(client: TestClient, email: str) -> int:
    keys = client.get("/admin/keys", headers=ADMIN).json()
    return next(k["id"] for k in keys if k["email"] == email)


def grant(client: TestClient, email: str, tokens: int) -> None:
    resp = client.post(f"/admin/keys/{key_id_for(client, email)}/quota", headers=ADMIN, json={"token_quota": tokens})
    assert resp.status_code == 200, resp.text
