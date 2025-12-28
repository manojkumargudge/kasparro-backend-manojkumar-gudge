import os
import sys
import time
import subprocess
from urllib.parse import urlparse

# ensure project root is on sys.path when run as a one-off container
sys.path.insert(0, os.getcwd())

def _pg_isready(host: str, port: int, user: str) -> bool:
    """Use pg_isready if available, else fall back to socket connect."""
    try:
        # try pg_isready first
        res = subprocess.run(["pg_isready", "-h", host, "-p", str(port), "-U", user], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return res.returncode == 0
    except FileNotFoundError:
        # fallback to socket
        import socket
        try:
            sock = socket.create_connection((host, port), timeout=3)
            sock.close()
            return True
        except Exception:
            return False


def _build_db_url() -> str:
    """Return DATABASE_URL from env or construct from POSTGRES_* vars."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    host = os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "db"))
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "kasparro")

    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def main() -> None:
    DATABASE_URL = _build_db_url()
    parsed = urlparse(DATABASE_URL)
    host = parsed.hostname or "db"
    port = parsed.port or 5432
    user = parsed.username or "postgres"

    print(f"Using DB URL host={host} port={port} user={user}")

    attempts = 0
    max_attempts = 30
    while attempts < max_attempts:
        attempts += 1
        if _pg_isready(host, port, user):
            print(f"DB is ready (attempt {attempts})")
            break
        print(f"DB not ready yet (attempt {attempts}/{max_attempts})")
        time.sleep(2)
    else:
        print("DB did not become ready; aborting")
        raise SystemExit(1)

    # Now import SQLAlchemy engine and models and create tables
    from sqlalchemy import create_engine

    sys.path.insert(0, os.getcwd())
    try:
        from core.models import Base
    except Exception as e:
        print("Failed to import core.models:", e)
        raise

    engine = create_engine(DATABASE_URL)
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("Done")


if __name__ == "__main__":
    main()
