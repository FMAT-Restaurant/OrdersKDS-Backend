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

Models
------
``target_metadata`` points at ``app.db.base.Base.metadata``.  Models must live in
``app/db/models.py`` (or the ``app.db.models`` package): this module imports it
so every table registers itself on ``Base.metadata`` and
``alembic revision --autogenerate`` can detect changes.  Until that module
exists (TASK-03) the import is skipped.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.db.base import Base

# ---------------------------------------------------------------------------
# Alembic Config object — gives access to values in alembic.ini.
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ---------------------------------------------------------------------------
# Load ``.env`` (repository root) for local runs.
# Variables already present in the process environment always win, so CI and
# containers (which inject DATABASE_URL) are never overridden by a file.
# ---------------------------------------------------------------------------
def _load_dotenv() -> None:
    """Populate ``os.environ`` from ``.env`` without overriding existing values."""
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()

# ---------------------------------------------------------------------------
# Override the database URL from the environment.
# This is the only place credentials may appear at runtime; they never live
# in alembic.ini or in committed files (DEVELOPMENT_GUIDELINES §10.2).
# ---------------------------------------------------------------------------
_database_url = os.environ.get("DATABASE_URL")
if not _database_url or "<" in _database_url:
    raise RuntimeError(
        "DATABASE_URL is not set (or still contains <placeholders>).\n"
        "Run 'python scripts/setup_env.py' to create .env with the local defaults, "
        "or export DATABASE_URL in your shell."
    )
# configparser treats '%' as interpolation syntax; escape it so URL-encoded
# passwords (e.g. 'p%40ss') do not break Alembic.
config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))

# ---------------------------------------------------------------------------
# Target metadata for --autogenerate support.
# Importing the models module registers every table on Base.metadata.  Only a
# missing ``app.db.models`` itself is tolerated (TASK-03 creates it); any other
# ImportError inside the models is a real bug and must surface.
# ---------------------------------------------------------------------------
try:
    importlib.import_module("app.db.models")
except ModuleNotFoundError as exc:
    if exc.name != "app.db.models":
        raise

target_metadata = Base.metadata


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
        compare_type=True,
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
