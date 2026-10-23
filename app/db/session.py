from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.logging import redact_url

_url = get_settings().database_url
try:
    engine = create_engine(
        _url,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {},
    )
except Exception as exc:  # noqa: BLE001 — SQLAlchemy quotes the full URL, password included
    raise RuntimeError(f"Invalid DATABASE_URL {redact_url(_url)}: {redact_url(exc)}") from None
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
