"""Wait for the database, then apply Alembic migrations (used by the compose init-db service)."""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.exc import OperationalError  # noqa: E402


def main() -> None:
    from core.migrations import run_migrations

    max_attempts = int(os.getenv("DB_INIT_ATTEMPTS", "30"))
    for attempt in range(1, max_attempts + 1):
        try:
            run_migrations()
            print("Database migrations applied")
            return
        except OperationalError:
            print(f"DB not ready yet (attempt {attempt}/{max_attempts})")
            time.sleep(2)
    print("DB did not become ready; aborting")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
