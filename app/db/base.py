"""Declarative base shared by every SQLAlchemy model.

All ORM models must inherit from :class:`Base` so that Alembic's
``--autogenerate`` can discover them through ``Base.metadata`` (see
``app/alembic/env.py``).

The naming convention below gives every constraint and index a deterministic
name.  Without it PostgreSQL generates arbitrary names, and Alembic cannot
reliably ``DROP`` them in a ``downgrade`` (required by IT-19:
``upgrade head`` / ``downgrade base`` without errors).
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Parent class of all ORM models (SQLAlchemy 2.x declarative style)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
