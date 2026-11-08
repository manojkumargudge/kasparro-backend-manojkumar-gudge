FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .


# Test image: runtime + dev tooling. Used by `make test` (compose service "test").
FROM base AS test
RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["pytest", "-q"]


# Runtime image (default target). Migrations run on startup.
FROM base AS runtime

EXPOSE 8000

# Healthcheck: ping /health without requiring curl in the image
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=5).read()"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
