"""Nightwarden: an AI on-call agent that watches Citebase."""
import html
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.collector import poller
from app.core.config import get_settings
from app.core.logging import configure_logging, redact_url
from app.core.security import RateLimit, SecurityHeaders
from app.db.models import Incident
from app.db.session import get_db
from app.routes import api, health, ops

settings = get_settings()
configure_logging(settings.log_json)
logger = logging.getLogger("nightwarden")
STATIC = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Database: %s", redact_url(settings.database_url))
    stop = poller.start() if settings.poller_enabled and not settings.vercel else None
    if not settings.groq_api_key:
        logger.warning("GROQ_API_KEY not set: triage runs in degraded (rules-only) mode")
    missing = [k for k, v in (("BREVO_API_KEY", settings.brevo_api_key), ("ALERT_EMAIL", settings.alert_email)) if not v]
    if missing:
        logger.warning("Email alerts are off: %s not set; pages go to the webhook only", " and ".join(missing))
    yield
    if stop:
        stop.set()


app = FastAPI(title="Nightwarden", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(RateLimit)
app.add_middleware(SecurityHeaders)
app.include_router(health.router)
app.include_router(api.router)
app.include_router(ops.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _index() -> str:
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as fh:
        return fh.read()


@app.get("/", include_in_schema=False)
@app.get("/live", include_in_schema=False)
@app.get("/incidents", include_in_schema=False)
@app.get("/eval", include_in_schema=False)
@app.get("/how", include_in_schema=False)
@app.get("/usage", include_in_schema=False)
def dashboard():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/incidents/{incident_id}", include_in_schema=False)
def incident_page(incident_id: str, db: Session = Depends(get_db)):
    """Shareable incident link: the same page, with the incident's summary as its title for link previews."""
    page = _index()
    inc = db.get(Incident, incident_id[:36])
    if inc is not None:
        title = html.escape(f"{inc.severity.upper()} · {inc.summary} · Nightwarden", quote=True)
        page = page.replace("<title>Nightwarden</title>",
                            f'<title>{title}</title>\n<meta property="og:title" content="{title}">', 1)
    return HTMLResponse(page, status_code=200 if inc is not None else 404)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(os.path.join(STATIC, "favicon.svg"), media_type="image/svg+xml")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    """Crawlers fetching the JSON API cost database reads; the pages are fine to index."""
    return PlainTextResponse("User-agent: *\nDisallow: /api/\n")
