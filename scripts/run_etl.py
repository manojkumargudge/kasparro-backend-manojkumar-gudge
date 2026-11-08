"""One-shot ETL runner (used by `make etl`, the Docker image and the cloud scheduled job).

Applies migrations, then runs every source (``ETL_SOURCES`` narrows the list). Exit code 1
if any source run failed and ``FAIL_ON_ERROR`` is truthy (default). Records rejected by
validation make a run ``partial`` but do not fail the process.
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger import get_logger  # noqa: E402

log = get_logger(__name__)


def run() -> int:
    try:
        from core.config import env_bool
        from core.migrations import run_migrations
        from services.etl import run_all

        run_migrations()
    except Exception:
        log.exception("ETL setup failed (imports or migrations)")
        return 1

    results = run_all()
    failed = [r.source for r in results if r.status == "failed"]
    if failed and env_bool("FAIL_ON_ERROR", True):
        log.error("ETL finished with failed sources: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
