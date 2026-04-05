"""
Jarvis - Terminal-first AI coding agent backend.
FastAPI application entry point.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as api_router
from app.db.session import init_db, close_db
from app.config import settings
from app.middleware import AuthMiddleware, RateLimitMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup, cleanup on shutdown."""
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="Jarvis",
    description="Terminal-first AI coding agent backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security middleware (applied in reverse order of addition)
app.add_middleware(RateLimitMiddleware, limit=100, window=60)  # 100 req/min per IP
if settings.api_key:
    app.add_middleware(AuthMiddleware, api_key=settings.api_key)

app.include_router(api_router, prefix="/api")
