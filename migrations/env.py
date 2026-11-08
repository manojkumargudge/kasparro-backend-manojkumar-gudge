"""Alembic environment.

Two entry points:
* ``core.migrations.run_migrations()`` passes an open connection via
  ``config.attributes["connection"]`` (used by API startup and the ETL runner).
* The ``alembic`` CLI, which builds an engine from ``DATABASE_URL``.
"""

from alembic import context
from sqlalchemy import Connection, create_engine, pool

import core.models  # noqa: F401  (registers tables on Base.metadata)
from core.db import Base, get_database_url

config = context.config
target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(url=get_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_engine(get_database_url(), poolclass=pool.NullPool)
    with engine.begin() as conn:
        _run(conn)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
