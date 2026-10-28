"""Performance and period reports for the Insights pages (public, read-only, edge-cached)."""
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import reports

router = APIRouter(prefix="/api")
_cache: dict[str, tuple[float, object]] = {}
TTL_S = 60


def _cached(key: str, build):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    value = build()
    _cache[key] = (time.monotonic(), value)
    return value


@router.get("/performance")
def performance(days: int = 7, db: Session = Depends(get_db)):
    if days not in reports.PERFORMANCE_DAYS:
        raise HTTPException(status_code=422, detail="days must be 7, 30 or 365")
    return _cached(f"perf:{days}", lambda: reports.performance(db, days))


@router.get("/reports")
def period_report(period: str = "week", offset: int = 0, format: str = "json", db: Session = Depends(get_db)):
    if period not in reports.PERIODS:
        raise HTTPException(status_code=422, detail="period must be week, month or year")
    if not 0 <= offset <= 520:
        raise HTTPException(status_code=422, detail="offset must be between 0 and 520")
    rep = _cached(f"rep:{period}:{offset}", lambda: reports.report(db, period, offset))
    if format == "csv":
        name = f"nightwarden-{period}-{rep['start']}.csv"
        return PlainTextResponse(reports.to_csv(rep), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})
    return rep
