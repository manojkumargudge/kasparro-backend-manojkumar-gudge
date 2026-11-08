from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from services.metrics import REGISTRY

router = APIRouter(tags=["ops"])

_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus exposition format. Public (no auth); contains no secrets."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@router.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(_STATIC_DIR / "dashboard.html")
