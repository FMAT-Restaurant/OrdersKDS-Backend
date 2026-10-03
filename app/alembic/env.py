"""Alembic environment configuration for Orders & KDS Backend.

This module is executed by every Alembic command (``alembic upgrade``,
``alembic revision``, etc.).  It reads the database URL from the
``DATABASE_URL`` environment variable so that credentials are never
hardcoded in ``alembic.ini`` or in version control.

Async setup
-----------
SQLAlchemy 2 + asyncpg require running migrations through an async engine.
Alembic provides ``run_sync`` inside an async context for this purpose.
See: https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic

NOTE: ORM models are not imported here yet because the schema has not been
defined.  When you add the first model in ``app/db/models.py``, uncomment the
import below and point ``target_metadata`` at ``Base.metadata`` so that
``alembic revision --autogenerate`` can detect changes automatically.

TODO(RF-12, RF-13): Import Base once app/db/models.py defines the orders table.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ---------------------------------------------------------------------------
# Alembic Config object — gives access to values in alembic.ini.
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Override the database URL from the environment.
# This is the only place credentials may appear at runtime; they never live
# in alembic.ini or in committed files (DEVELOPMENT_GUIDELINES §10.2).
# ---------------------------------------------------------------------------
_database_url = os.environ.get("DATABASE_URL")
if not _database_url:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set.\n"
        "Run 'python scripts/setup_env.py' to create .env, then load it:\n"
        "  export $(grep -v '^#' .env | xargs)\n"
        "or use 'python-dotenv' to load it automatically."
    )
config.set_main_option("sqlalchemy.url", _database_url)

# ---------------------------------------------------------------------------
# Target metadata for --autogenerate support.
# Uncomment the import below once app/db/models.py exists and Base is defined.
# ---------------------------------------------------------------------------
# from app.db.models import Base  # noqa: E402
# target_metadata = Base.metadata
target_metadata = None  # Replace with Base.metadata when models are defined.


# ---------------------------------------------------------------------------
# Offline migrations (generate SQL without a live DB connection).
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL, not an Engine.  Calls to
    ``context.execute()`` emit the SQL to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Use batch mode for SQLite compatibility in tests (no-op on Postgres).
        render_as_batch=False,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migrations (run against a live database connection).
# ---------------------------------------------------------------------------
def _do_run_migrations(connection: Connection) -> None:
    """Execute migrations synchronously within an async connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Compare column types for --autogenerate (catches type changes).
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations inside it."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No connection pool needed for migration runs.
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using asyncio."""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Entry point: Alembic calls this module and checks context.is_offline_mode().
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
