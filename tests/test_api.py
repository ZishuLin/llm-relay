import json

import httpx
import pytest

from conftest import ADMIN, grant, register, solve_captcha
from pricing import estimate_cost


def chat_ok(prompt_tokens=12, completion_tokens=30, content="Hi there"):
    return httpx.Response(200, json={
        "id": "chatcmpl-1", "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    })


def sse(*events: dict) -> httpx.Response:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def delta(text: str) -> dict:
    return {"choices": [{"index": 0, "delta": {"content": text}}]}


def auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


# ---------- CAPTCHA and sign-up ----------

def test_captcha_image_and_single_use_token(client):
    resp = client.get("/captcha/image")
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    token, code = solve_captcha(client)
    bad = client.post("/user/register", json={"email": "c@example.com", "password": "s3cret-pass",
                                              "captcha_token": token, "captcha_code": "ZZZZ" if code != "ZZZZ" else "YYYY"})
    assert bad.status_code == 400
    # The token was consumed by the wrong guess, so the right code no longer works either.
    again = client.post("/user/register", json={"email": "c@example.com", "password": "s3cret-pass",
                                                "captcha_token": token, "captcha_code": code})
    assert again.status_code == 400


def test_register_login_and_me(client):
    data = register(client, "alice@example.com")
    assert data["keys"][0]["key"].startswith("sk-relay-")
    assert data["keys"][0]["token_quota"] == 0

    me = client.get("/user/me", headers={"X-User-Token": data["token"]})
    assert me.status_code == 200 and me.json()["email"] == "alice@example.com"

    token, code = solve_captcha(client)
    login = client.post("/user/login", json={"email": "alice@example.com", "password": "s3cret-pass",
                                             "captcha_token": token, "captcha_code": code})
    assert login.status_code == 200
    # Logging in issues a new session token; the old one stops working.
    assert client.get("/user/me", headers={"X-User-Token": data["token"]}).status_code == 401
    assert client.get("/user/me", headers={"X-User-Token": login.json()["token"]}).status_code == 200

    token, code = solve_captcha(client)
    wrong = client.post("/user/login", json={"email": "alice@example.com", "password": "nope-nope",
                                             "captcha_token": token, "captcha_code": code})
    assert wrong.status_code == 401


def test_admin_endpoints_require_token(client):
    assert client.get("/admin/keys").status_code == 422  # header missing
    assert client.get("/admin/keys", headers={"x-admin-token": "wrong"}).status_code == 403
    assert client.get("/admin/keys", headers=ADMIN).status_code == 200


# ---------- Quotas and usage tracking ----------

def test_quota_gates_requests_and_usage_is_tracked(client, pool, upstream):
    user = register(client, "bob@example.com")
    key = user["keys"][0]["key"]
    upstream.handler = lambda req: chat_ok(prompt_tokens=12, completion_tokens=30)
    body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "Hello"}]}

    # No quota yet: refused before anything is sent upstream.
    assert client.post("/v1/chat/completions", headers=auth(key), json=body).status_code == 429
    assert upstream.requests == []

    grant(client, "bob@example.com", 50)
    resp = client.post("/v1/chat/completions", headers=auth(key), json=body)
    assert resp.status_code == 200, resp.text
    info = resp.json()["_relay_info"]
    assert (info["input_tokens"], info["output_tokens"], info["tokens_remaining"]) == (12, 30, 8)
    assert json.loads(upstream.requests[0].content)["model"] == "deepseek-chat"

    me = client.get("/user/me", headers={"X-User-Token": user["token"]}).json()["keys"][0]
    assert (me["tokens_used"], me["tokens_remaining"]) == (42, 8)

    stats = client.get("/admin/stats?days=1", headers=ADMIN).json()
    row = next(r for r in stats["by_model"] if r["model"] == "deepseek-chat")
    assert row["requests"] >= 1 and row["input_tokens"] >= 12 and row["output_tokens"] >= 30
    assert row["est_cost_usd"] >= round(estimate_cost("deepseek-chat", 12, 30), 6)

    # A second request uses up the rest of the quota; the third is refused.
    assert client.post("/v1/chat/completions", headers=auth(key), json=body).status_code == 200
    assert client.post("/v1/chat/completions", headers=auth(key), json=body).status_code == 429


def test_estimate_cost_has_no_markup():
    # deepseek-chat: $0.27 in / $1.10 out per million tokens
    assert estimate_cost("deepseek-chat", 1_000_000, 1_000_000) == pytest.approx(1.37)
    assert estimate_cost("llama-3.3-70b", 5000, 5000) == 0.0
    assert estimate_cost("unknown-model", 10, 10) == 0.0


def test_extra_openai_params_pass_through(client, pool, upstream):
    key = register(client, "params@example.com")["keys"][0]["key"]
    grant(client, "params@example.com", 1000)
    upstream.handler = lambda req: chat_ok()
    resp = client.post("/v1/chat/completions", headers=auth(key), json={
        "model": "llama-3.3-70b", "messages": [{"role": "user", "content": "x"}], "temperature": 0.2, "top_p": 0.9,
    })
    assert resp.status_code == 200
    sent = json.loads(upstream.requests[0].content)
    assert sent["model"] == "llama-3.3-70b-versatile"
    assert (sent["temperature"], sent["top_p"]) == (0.2, 0.9)


def test_unsupported_model(client, pool, upstream):
    key = register(client, "model@example.com")["keys"][0]["key"]
    grant(client, "model@example.com", 1000)
    resp = client.post("/v1/chat/completions", headers=auth(key), json={"model": "nope", "messages": []})
    assert resp.status_code == 400


# ---------- Upstream key rotation ----------

def test_upstream_429_rotates_to_next_key(client, pool, upstream):
    key = register(client, "carol@example.com")["keys"][0]["key"]
    grant(client, "carol@example.com", 10_000)

    def handler(req):
        if req.headers["authorization"] == "Bearer sk-test-deepseek-1":
            return httpx.Response(429, json={"error": "rate limited"})
        return chat_ok()

    upstream.handler = handler
    body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "Hello"}]}
    resp = client.post("/v1/chat/completions", headers=auth(key), json=body)
    assert resp.status_code == 200
    assert [r.headers["authorization"] for r in upstream.requests] == ["Bearer sk-test-deepseek-1", "Bearer sk-test-deepseek-2"]
    assert client.get("/admin/pool", headers=ADMIN).json()["deepseek"] == {"total": 2, "available": 1}

    # While key 1 cools down, every request goes straight to key 2.
    upstream.requests.clear()
    client.post("/v1/chat/completions", headers=auth(key), json=body)
    client.post("/v1/chat/completions", headers=auth(key), json=body)
    assert {r.headers["authorization"] for r in upstream.requests} == {"Bearer sk-test-deepseek-2"}


def test_all_upstream_keys_rate_limited_returns_503(client, pool, upstream):
    key = register(client, "dave@example.com")["keys"][0]["key"]
    grant(client, "dave@example.com", 10_000)
    upstream.handler = lambda req: httpx.Response(429)
    resp = client.post("/v1/chat/completions", headers=auth(key),
                       json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "Hello"}]})
    assert resp.status_code == 503
    assert len(upstream.requests) == 2
    assert pool.stats()["deepseek"]["available"] == 0


# ---------- Streaming ----------

def test_streaming_relays_chunks_and_records_reported_usage(client, pool, upstream):
    user = register(client, "erin@example.com")
    key = user["keys"][0]["key"]
    grant(client, "erin@example.com", 10_000)
    upstream.handler = lambda req: sse(delta("Hel"), delta("lo"),
                                       {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}})

    with client.stream("POST", "/v1/chat/completions", headers=auth(key), json={
        "model": "llama-3.3-70b", "stream": True, "messages": [{"role": "user", "content": "Hi"}],
    }) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "".join(resp.iter_text())

    assert '"Hel"' in text and '"lo"' in text and "data: [DONE]" in text
    sent = json.loads(upstream.requests[0].content)
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}

    me = client.get("/user/me", headers={"X-User-Token": user["token"]}).json()["keys"][0]
    assert me["tokens_used"] == 10


def test_streaming_without_usage_falls_back_to_estimate(client, pool, upstream):
    user = register(client, "frank@example.com")
    key = user["keys"][0]["key"]
    grant(client, "frank@example.com", 10_000)
    upstream.handler = lambda req: sse(delta("a"), delta("b"), delta("c"))

    with client.stream("POST", "/v1/chat/completions", headers=auth(key), json={
        "model": "llama-3.3-70b", "stream": True, "messages": [{"role": "user", "content": "12345678"}],
    }) as resp:
        "".join(resp.iter_text())

    me = client.get("/user/me", headers={"X-User-Token": user["token"]}).json()["keys"][0]
    assert me["tokens_used"] == 2 + 3  # ~8 chars / 4 in, one token per content chunk out
