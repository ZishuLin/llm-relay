"""User accounts.

POST /user/register  sign up (CAPTCHA required); creates a default API key
POST /user/login     log in (CAPTCHA required)
POST /user/logout    end the session
GET  /user/me        current user, keys and remaining quota (X-User-Token header)
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import settings
from auth import generate_key, generate_session_token, hash_password, verify_password
from captcha import verify_captcha
from models import APIKey, User, get_db

router = APIRouter(prefix="/user")

MIN_PASSWORD_LENGTH = 8


class AuthReq(BaseModel):
    email: str
    password: str
    captcha_token: str
    captcha_code: str


def key_view(k: APIKey) -> dict:
    return {
        "key": k.key,
        "name": k.name,
        "token_quota": k.token_quota,
        "tokens_used": k.tokens_used,
        "tokens_remaining": k.tokens_remaining,
        "active": k.is_active,
    }


async def user_keys(db: AsyncSession, user_id: int) -> list[dict]:
    keys = (await db.execute(select(APIKey).where(APIKey.user_id == user_id))).scalars().all()
    return [key_view(k) for k in keys]


async def get_current_user(x_user_token: str = Header(...), db: AsyncSession = Depends(get_db)) -> User:
    user = (await db.execute(select(User).where(User.session_token == x_user_token))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(401, "Please log in")
    return user


@router.post("/register")
async def register(req: AuthReq, db: AsyncSession = Depends(get_db)):
    if not verify_captcha(req.captcha_token, req.captcha_code):
        raise HTTPException(400, "Wrong or expired CAPTCHA")
    email = req.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Enter a valid email address")
    if len(req.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(400, "This email is already registered")

    user = User(email=email, password_hash=hash_password(req.password), session_token=generate_session_token())
    db.add(user)
    await db.flush()
    db.add(APIKey(key=generate_key(), name="Default key", user_id=user.id, token_quota=settings.DEFAULT_TOKEN_QUOTA))
    await db.commit()

    return {"token": user.session_token, "email": user.email, "keys": await user_keys(db, user.id)}


@router.post("/login")
async def login(req: AuthReq, db: AsyncSession = Depends(get_db)):
    if not verify_captcha(req.captcha_token, req.captcha_code):
        raise HTTPException(400, "Wrong or expired CAPTCHA")
    user = (await db.execute(select(User).where(User.email == req.email.strip().lower()))).scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Wrong email or password")
    if not user.is_active:
        raise HTTPException(403, "This account is disabled")

    user.session_token = generate_session_token()
    await db.commit()
    return {"token": user.session_token, "email": user.email, "keys": await user_keys(db, user.id)}


@router.post("/logout")
async def logout(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user.session_token = None
    await db.commit()
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return {"email": user.email, "keys": await user_keys(db, user.id)}
