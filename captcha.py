"""Image CAPTCHA for sign-up and login.

GET /captcha/image returns a PNG plus a one-time token in the X-Captcha-Token
header. Codes live in memory for 5 minutes (use Redis or similar when running
more than one worker).
"""

import random
import secrets
import time
from io import BytesIO

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(prefix="/captcha")

CAPTCHA_TTL_SECONDS = 300
CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no easily confused characters (0/O, 1/I)

_store: dict[str, tuple[str, float]] = {}


def _gen_code(length: int = 4) -> str:
    return "".join(secrets.choice(CHARS) for _ in range(length))


def _purge_expired(now: float):
    for token in [t for t, (_, exp) in _store.items() if exp < now]:
        del _store[token]


def _draw_captcha(code: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 120, 40
    img = Image.new("RGB", (width, height), tuple(random.randint(240, 255) for _ in range(3)))
    draw = ImageDraw.Draw(img)

    for _ in range(4):  # noise lines
        draw.line(
            [(random.randint(0, width), random.randint(0, height)), (random.randint(0, width), random.randint(0, height))],
            fill=tuple(random.randint(100, 200) for _ in range(3)),
            width=1,
        )
    for _ in range(30):  # noise dots
        draw.point((random.randint(0, width), random.randint(0, height)), fill=(random.randint(100, 200),) * 3)

    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except OSError:
        font = ImageFont.load_default(size=22)
    for i, ch in enumerate(code):
        draw.text(
            (10 + i * 26 + random.randint(-3, 3), random.randint(5, 12)),
            ch,
            fill=tuple(random.randint(20, 120) for _ in range(3)),
            font=font,
        )

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def issue_captcha() -> tuple[str, str]:
    """Create a code and its one-time token; returns (token, code)."""
    now = time.time()
    _purge_expired(now)
    code = _gen_code()
    token = secrets.token_urlsafe(16)
    _store[token] = (code, now + CAPTCHA_TTL_SECONDS)
    return token, code


@router.get("/image")
async def get_captcha():
    token, code = issue_captcha()
    return Response(
        content=_draw_captcha(code),
        media_type="image/png",
        headers={"X-Captcha-Token": token, "Cache-Control": "no-store"},
    )


def verify_captcha(token: str, code: str) -> bool:
    """Check a code. A token is consumed on its first check, right or wrong."""
    entry = _store.pop(token, None)
    if not entry:
        return False
    stored_code, expires_at = entry
    return time.time() <= expires_at and stored_code == (code or "").strip().upper()
