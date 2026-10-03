"""Local environment bootstrap script.

Reads .env.example and produces a .env file ready for local development.
Variables that have a sensible default for the docker-compose.yml setup are
filled in automatically; secrets that cannot be guessed are left as-is with an
inline instruction so the developer knows exactly what to replace.

Usage:
    python scripts/setup_env.py

The script is idempotent: if .env already exists it refuses to overwrite it
unless --force is passed, to avoid accidentally destroying a working setup.

NOTE: This script is for LOCAL DEVELOPMENT ONLY.
      CI/CD secrets live in GitHub Secrets (Settings > Secrets and variables >
      Actions).  Never run this script in a CI environment.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults that are safe for the local docker-compose.yml setup.
# Every value here matches the service definitions in docker-compose.yml so
# that a fresh clone works with a single command sequence:
#   docker compose up -d && python scripts/setup_env.py && alembic upgrade head
#
# IMPORTANT: These defaults contain the docker-compose default credentials.
#            They are intentionally weak and must NEVER be used in production.
# ---------------------------------------------------------------------------
_COMPOSE_DEFAULTS: dict[str, str] = {
    "DATABASE_URL": "postgresql+asyncpg://ordenes:ordenes@localhost:5432/ordenes",
    "REDIS_URL": "redis://localhost:6379/0",
    "RABBITMQ_URL": "amqp://ordenes:ordenes@localhost:5672/",
    "OUTBOX_POLL_INTERVAL_SECONDS": "1",
    "LOG_LEVEL": "INFO",
}

# Variables that have no safe default and MUST be set by the developer before
# starting the service.  The placeholder text is descriptive so the developer
# knows exactly what value is expected.
_REQUIRED_PLACEHOLDERS: dict[str, str] = {
    # OP-01: the timeout threshold must be agreed by the team.
    # Use an integer number of seconds (e.g. 60).
    "SAGA_TIMEOUT_SECONDS": "<agree-with-team: integer seconds, e.g. 60>",
}

# Optional variables — left empty until the corresponding open points are
# resolved (see DEVELOPMENT_GUIDELINES §1.2).
_OPTIONAL_EMPTY: dict[str, str] = {
    # OP-07: fill in once the Menu & Catalog team publishes the endpoint.
    "CATALOG_SYNC_URL": "",
    "CATALOG_RECONCILIATION_INTERVAL_SECONDS": "",
}


def _parse_example(example_path: Path) -> list[str]:
    """Return the lines of .env.example as-is to preserve comments."""
    return example_path.read_text(encoding="utf-8").splitlines()


def _build_env_content() -> str:
    """Build the .env content with defaults injected where available.

    Returns:
        A multi-line string ready to be written to .env.
    """
    all_values: dict[str, str] = {
        **_COMPOSE_DEFAULTS,
        **_REQUIRED_PLACEHOLDERS,
        **_OPTIONAL_EMPTY,
    }

    example_path = Path(".env.example")
    if not example_path.exists():
        sys.exit(
            "ERROR: .env.example not found.  Run this script from the "
            "repository root."
        )

    lines = _parse_example(example_path)
    result: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Preserve blank lines and comment lines verbatim.
        if not stripped or stripped.startswith("#"):
            result.append(line)
            continue

        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in all_values:
                result.append(f"{key}={all_values[key]}")
                continue

        # Any line not matched above is kept verbatim (future variables).
        result.append(line)

    return "\n".join(result) + "\n"


def _warn_about_placeholders(content: str) -> None:
    """Print a warning for every variable that still needs a real value."""
    unset = [
        line.split("=", 1)[0]
        for line in content.splitlines()
        if "=" in line
        and not line.strip().startswith("#")
        and "<" in line.split("=", 1)[1]
    ]
    if unset:
        print("\n The following variables still need a real value in .env:")
        for var in unset:
            print(f"   - {var}")
        print("   Edit .env before starting the service.\n")


def main() -> None:
    """Entry point for the environment bootstrap script."""
    parser = argparse.ArgumentParser(
        description="Generate .env from .env.example with local defaults."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing .env file.",
    )
    args = parser.parse_args()

    env_path = Path(".env")

    if env_path.exists() and not args.force:
        print(
            f"INFO: {env_path} already exists.  "
            "Pass --force to overwrite it."
        )
        sys.exit(0)

    content = _build_env_content()
    env_path.write_text(content, encoding="utf-8")

    print(f"[OK] {env_path} created successfully.")

    _warn_about_placeholders(content)

    # Remind the developer about the next steps.
    print(
        "Next steps:\n"
        "  1. Set SAGA_TIMEOUT_SECONDS in .env (agree the value with the team).\n"
        "  2. docker compose up -d postgres redis rabbitmq\n"
        "  3. alembic upgrade head\n"
        "  4. uvicorn app.main:app --reload --port 8000\n"
    )


if __name__ == "__main__":
    # Guard: never run in a CI environment to avoid leaking secrets.
    if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
        sys.exit(
            "ERROR: setup_env.py must not run in CI.  "
            "Use GitHub Secrets for CI/CD configuration."
        )
    main()
