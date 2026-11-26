"""Admin endpoints (header x-admin-token: <ADMIN_TOKEN>).

GET  /admin/keys              all keys with owner, quota and usage
POST /admin/keys              create a key with a token quota
POST /admin/keys/{id}/quota   set or add to a key's token quota
POST /admin/keys/{id}/active  enable or disable a key
GET  /admin/stats?days=N      requests, tokens and estimated upstream cost
GET  /admin/pool              upstream key pool status
"""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import generate_key, verify_admin
from key_pool import key_pool
from models import APIKey, UsageLog, User, get_db

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin)])


class CreateKeyReq(BaseModel):
    name: str = ""
    token_quota: int = Field(0, ge=0)


class QuotaReq(BaseModel):
    token_quota: int = Field(..., ge=0)
    mode: Literal["set", "add"] = "set"


class ActiveReq(BaseModel):
    active: bool


def admin_key_view(k: APIKey, email: str | None) -> dict:
    return {
        "id": k.id,
        "key_preview": f"{k.key[:9]}…{k.key[-4:]}",
        "name": k.name,
        "email": email,
        "token_quota": k.token_quota,
        "tokens_used": k.tokens_used,
        "tokens_remaining": k.tokens_remaining,
        "active": k.is_active,
        "last_used": k.last_used.isoformat(timespec="seconds") if k.last_used else None,
    }


async def _get_key(db: AsyncSession, key_id: int) -> APIKey:
    k = await db.get(APIKey, key_id)
    if not k:
        raise HTTPException(404, "Key not found")
    return k


@router.get("/keys")
async def list_keys(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(APIKey, User.email).outerjoin(User, User.id == APIKey.user_id).order_by(APIKey.id)
    )).all()
    return [admin_key_view(k, email) for k, email in rows]


@router.post("/keys")
async def create_key(req: CreateKeyReq, db: AsyncSession = Depends(get_db)):
    k = APIKey(key=generate_key(), name=req.name, token_quota=req.token_quota)
    db.add(k)
    await db.commit()
    return {**admin_key_view(k, None), "key": k.key}  # the full key is shown only once


@router.post("/keys/{key_id}/quota")
async def set_quota(key_id: int, req: QuotaReq, db: AsyncSession = Depends(get_db)):
    k = await _get_key(db, key_id)
    k.token_quota = req.token_quota if req.mode == "set" else (k.token_quota or 0) + req.token_quota
    await db.commit()
    return admin_key_view(k, None)


@router.post("/keys/{key_id}/active")
async def set_active(key_id: int, req: ActiveReq, db: AsyncSession = Depends(get_db)):
    k = await _get_key(db, key_id)
    k.is_active = req.active
    await db.commit()
    return admin_key_view(k, None)


@router.get("/stats")
async def stats(days: int = Query(1, ge=1, le=365), db: AsyncSession = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    rows = (await db.execute(
        select(
            UsageLog.model,
            func.count(UsageLog.id),
            func.coalesce(func.sum(UsageLog.input_tokens), 0),
            func.coalesce(func.sum(UsageLog.output_tokens), 0),
            func.coalesce(func.sum(UsageLog.est_cost_usd), 0.0),
            func.coalesce(func.avg(UsageLog.latency_ms), 0),
        ).where(UsageLog.created_at >= since).group_by(UsageLog.model)
    )).all()
    by_model = sorted(
        (
            {"model": m, "requests": n, "input_tokens": i, "output_tokens": o,
             "total_tokens": i + o, "est_cost_usd": round(c, 6), "avg_latency_ms": int(lat)}
            for m, n, i, o, c, lat in rows
        ),
        key=lambda r: r["total_tokens"], reverse=True,
    )
    return {
        "days": days,
        "requests": sum(r["requests"] for r in by_model),
        "input_tokens": sum(r["input_tokens"] for r in by_model),
        "output_tokens": sum(r["output_tokens"] for r in by_model),
        "total_tokens": sum(r["total_tokens"] for r in by_model),
        "est_cost_usd": round(sum(r["est_cost_usd"] for r in by_model), 6),
        "by_model": by_model,
    }


@router.get("/pool")
async def pool():
    return key_pool.stats()
