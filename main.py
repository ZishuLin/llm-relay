import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import settings
from admin import router as admin_router
from captcha import router as captcha_router
from key_pool import key_pool
from models import init_db
from router import router as relay_router
from user_api import router as user_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if not settings.ADMIN_TOKEN:
        logger.warning("ADMIN_TOKEN is empty: the admin API is locked until you set it")
    logger.info("Started | port=%s | proxy=%s", settings.PORT, "enabled" if settings.PROXY_URL else "disabled")
    yield


app = FastAPI(title="LLM Relay", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    expose_headers=["X-Captcha-Token"],
)
app.include_router(relay_router)
app.include_router(admin_router)
app.include_router(user_router)
app.include_router(captcha_router)


@app.get("/health")
async def health():
    return {"status": "ok", "pool": key_pool.stats()}


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return FileResponse(BASE_DIR / "admin.html")


@app.get("/user", include_in_schema=False)
async def user_portal():
    return FileResponse(BASE_DIR / "user.html")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT)
