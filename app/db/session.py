from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.logging import redact_url

_url = get_settings().database_url
_sqlite = _url.startswith("sqlite")
try:
    # Postgres (Neon): no pool. Each poll/request opens a connection and closes it when the session
    # closes, so no idle pooled connection keeps Neon's compute awake (it suspends after 5 idle minutes
    # and bills CU-hours while awake). Neon's own pooler (-pooler host) makes the connect cheap.
    engine = create_engine(
        _url,
        **({"connect_args": {"check_same_thread": False}, "pool_pre_ping": True} if _sqlite
           else {"poolclass": NullPool}),
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
