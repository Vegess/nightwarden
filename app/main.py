"""Nightwarden: an AI on-call agent that watches Citebase."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.collector import poller
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import RateLimit, SecurityHeaders
from app.routes import api, health

settings = get_settings()
configure_logging(settings.log_json)
logger = logging.getLogger("nightwarden")
STATIC = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = poller.start() if settings.poller_enabled and not settings.vercel else None
    if not settings.groq_api_key:
        logger.warning("GROQ_API_KEY not set: triage runs in degraded (rules-only) mode")
    yield
    if stop:
        stop.set()


app = FastAPI(title="Nightwarden", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(RateLimit)
app.add_middleware(SecurityHeaders)
app.include_router(health.router)
app.include_router(api.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(os.path.join(STATIC, "index.html"))
