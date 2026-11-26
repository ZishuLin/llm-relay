from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from settings import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    session_token = Column(String, unique=True, index=True, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class APIKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, default="")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    token_quota = Column(Integer, default=0)  # total tokens this key may use, set by an admin
    tokens_used = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used = Column(DateTime, nullable=True)

    @property
    def tokens_remaining(self) -> int:
        return max(0, (self.token_quota or 0) - (self.tokens_used or 0))


class UsageLog(Base):
    """One row per proxied request, for monitoring usage and estimated upstream cost."""

    __tablename__ = "usage_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    api_key_id = Column(Integer, ForeignKey("api_keys.id"), index=True)
    model = Column(String)
    upstream_model = Column(String)
    provider = Column(String)
    stream = Column(Boolean, default=False)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    usage_estimated = Column(Boolean, default=False)  # True when the upstream sent no usage block
    est_cost_usd = Column(Float, default=0.0)  # estimated upstream cost, monitoring only
    latency_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
