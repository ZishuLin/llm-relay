import hashlib
import hmac
import secrets
from datetime import datetime

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import settings
from models import APIKey, get_db

security = HTTPBearer()

PBKDF2_ITERATIONS = 200_000


def generate_key() -> str:
    return f"sk-relay-{secrets.token_urlsafe(24)}"


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    """Salted PBKDF2-SHA256, stored as pbkdf2_sha256$iterations$salt$hash."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(candidate, digest)


async def get_current_key(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> APIKey:
    token = credentials.credentials
    key_obj = (await db.execute(select(APIKey).where(APIKey.key == token))).scalar_one_or_none()
    if not key_obj:
        raise HTTPException(401, "Invalid API key")
    if not key_obj.is_active:
        raise HTTPException(403, "API key disabled")
    if key_obj.tokens_remaining <= 0:
        raise HTTPException(429, "Token quota exhausted; ask an admin to raise it")
    await db.execute(update(APIKey).where(APIKey.id == key_obj.id).values(last_used=datetime.utcnow()))
    await db.commit()
    return key_obj


def verify_admin(x_admin_token: str = Header(...)):
    if not settings.ADMIN_TOKEN or not hmac.compare_digest(x_admin_token, settings.ADMIN_TOKEN):
        raise HTTPException(403, "Invalid admin token")
