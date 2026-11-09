from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from api.deps import read_client
from services.metrics import REGISTRY

router = APIRouter(tags=["ops"])

_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


@router.get("/metrics", include_in_schema=False, dependencies=[Depends(read_client)])
def metrics() -> Response:
    """Prometheus exposition format. Requires an API key (``read``); contains no secrets."""
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@router.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    """Static page; it calls the API with the key the user types in (kept in sessionStorage)."""
    return FileResponse(_STATIC_DIR / "dashboard.html")
