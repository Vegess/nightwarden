import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

router = APIRouter()
INDEX = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")


@router.get("/health")
def health(request: Request):
    """For uptime monitors: cheap, no database. A browser asking for HTML gets the Health page instead."""
    if "text/html" in request.headers.get("accept", ""):
        return FileResponse(INDEX)
    return {"status": "ok"}
