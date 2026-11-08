"""ASGI entry point (``uvicorn main:app``). The application lives in ``api.app``."""

from api.app import app

__all__ = ["app"]
