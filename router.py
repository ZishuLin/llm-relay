"""OpenAI-compatible proxy endpoints: /v1/models and /v1/chat/completions."""

import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import update

import settings
from auth import get_current_key
from key_pool import key_pool
from models import APIKey, AsyncSessionLocal, UsageLog
from pricing import MODELS, UPSTREAM_ENDPOINTS, estimate_cost

logger = logging.getLogger(__name__)
router = APIRouter()

# Providers that send a final usage chunk when asked with stream_options.include_usage.
STREAM_USAGE_PROVIDERS = {"openai", "deepseek", "groq"}


class ChatRequest(BaseModel):
    # Unknown OpenAI parameters (temperature, top_p, tools, ...) are passed through upstream.
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict]
    stream: bool = False


def get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(proxy=settings.PROXY_URL or None, timeout=httpx.Timeout(120.0, connect=10.0))


def build_headers(provider: str, key: str) -> dict:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def rough_token_count(messages: list[dict]) -> int:
    """About 4 characters per token; used only when the upstream reports no usage."""
    chars = sum(len(m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content"))) for m in messages)
    return max(1, chars // 4)


async def record_usage(api_key_id: int, model: str, stream: bool, input_tokens: int, output_tokens: int,
                       estimated: bool, started: float) -> None:
    cfg = MODELS[model]
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(APIKey).where(APIKey.id == api_key_id)
            .values(tokens_used=APIKey.tokens_used + input_tokens + output_tokens)
        )
        db.add(UsageLog(
            api_key_id=api_key_id, model=model, upstream_model=cfg["upstream"], provider=cfg["provider"],
            stream=stream, input_tokens=input_tokens, output_tokens=output_tokens, usage_estimated=estimated,
            est_cost_usd=estimate_cost(model, input_tokens, output_tokens),
            latency_ms=int((time.time() - started) * 1000),
        ))
        await db.commit()


async def send_with_rotation(client: httpx.AsyncClient, provider: str, url: str, body: dict, stream: bool) -> httpx.Response:
    """Send to the upstream, moving to the next pooled key whenever one answers 429."""
    last_error: Exception | None = None
    for _ in range(max(1, key_pool.size(provider))):
        key = key_pool.get_key(provider)
        if key is None:
            break
        request = client.build_request("POST", url, headers=build_headers(provider, key), json=body)
        try:
            resp = await client.send(request, stream=stream)
        except httpx.HTTPError as e:
            last_error = e
            logger.warning("Upstream %s request failed: %s", provider, e)
            continue
        if resp.status_code == 429:
            await resp.aclose()
            key_pool.mark_rate_limited(key)
            continue
        return resp
    if last_error is not None:
        raise HTTPException(502, f"Upstream request failed: {last_error}")
    raise HTTPException(503, f"No {provider} key available (none configured, or all rate limited)")


@router.get("/v1/models")
async def list_models(current_key: APIKey = Depends(get_current_key)):
    return {"object": "list", "data": [
        {"id": name, "object": "model", "owned_by": cfg["provider"]} for name, cfg in MODELS.items()
    ]}


@router.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, current_key: APIKey = Depends(get_current_key)):
    cfg = MODELS.get(req.model)
    if not cfg:
        raise HTTPException(400, f"Model '{req.model}' is not supported")

    provider = cfg["provider"]
    url = f"{UPSTREAM_ENDPOINTS[provider]}/chat/completions"
    body = {**req.model_dump(), "model": cfg["upstream"]}
    if req.stream and provider in STREAM_USAGE_PROVIDERS:
        body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}

    started = time.time()
    client = get_client()
    try:
        resp = await send_with_rotation(client, provider, url, body, stream=req.stream)
        if resp.status_code != 200:
            detail = (await resp.aread()).decode(errors="replace")[:500]
            await resp.aclose()
            raise HTTPException(resp.status_code, f"Upstream error: {detail}")
    except BaseException:
        await client.aclose()
        raise

    if req.stream:
        return StreamingResponse(
            _relay_stream(client, resp, req, current_key.id, started),
            media_type="text/event-stream",
            headers={"X-Relay-Model": cfg["upstream"], "Cache-Control": "no-cache"},
        )

    try:
        data = resp.json()
    finally:
        await resp.aclose()
        await client.aclose()

    usage = data.get("usage") or {}
    in_t, out_t = usage.get("prompt_tokens"), usage.get("completion_tokens")
    estimated = in_t is None or out_t is None
    if estimated:
        in_t = rough_token_count(req.messages)
        out_t = rough_token_count([m.get("message", {}) for m in data.get("choices", [])])
    await record_usage(current_key.id, req.model, False, in_t, out_t, estimated, started)

    data["_relay_info"] = {
        "upstream_model": cfg["upstream"],
        "input_tokens": in_t,
        "output_tokens": out_t,
        "tokens_remaining": max(0, current_key.tokens_remaining - in_t - out_t),
    }
    return JSONResponse(data)


async def _relay_stream(client: httpx.AsyncClient, resp: httpx.Response, req: ChatRequest, api_key_id: int, started: float):
    in_t = out_t = 0
    usage_found = False
    content_chunks = 0
    try:
        async for line in resp.aiter_lines():
            if not line:
                continue
            yield (line + "\n\n").encode()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
            if usage:
                in_t = usage.get("prompt_tokens", 0)
                out_t = usage.get("completion_tokens", 0)
                usage_found = True
            elif chunk.get("choices"):
                content_chunks += 1
    except httpx.HTTPError as e:
        logger.error("Upstream stream failed: %s", e)
        yield b'data: {"error": "upstream_stream_failed"}\n\n'
    finally:
        await resp.aclose()
        await client.aclose()
        if not usage_found:  # fall back to an estimate: about one token per content chunk
            in_t, out_t = rough_token_count(req.messages), content_chunks
        await record_usage(api_key_id, req.model, True, in_t, out_t, not usage_found, started)
