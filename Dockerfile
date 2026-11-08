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


# Runtime image (default target). The same image runs the API (default CMD) and the
# ETL job (`python scripts/run_etl.py`). Migrations run on API startup and before each ETL.
FROM base AS runtime

LABEL org.opencontainers.image.source="https://github.com/manojkumargudge/kasparro-backend-manojkumar-gudge"

# Cloud Run injects PORT; locally it defaults to 8000.
ENV PORT=8000
EXPOSE 8000

# /health returns 503 when the database is unreachable (no curl needed in the image).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request as u; u.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8000'), timeout=5)"

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
