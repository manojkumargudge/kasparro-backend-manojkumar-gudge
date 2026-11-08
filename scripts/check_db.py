"""Print the (password-masked) database URL and try to connect."""

import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set")
        return 1
    print("DATABASE_URL:", make_url(url).render_as_string(hide_password=True))
    try:
        with create_engine(url).connect():
            print("connected to database")
        return 0
    except Exception as exc:
        print("ERROR:", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
