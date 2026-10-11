from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    """For uptime monitors: cheap, no database."""
    return {"status": "ok"}
