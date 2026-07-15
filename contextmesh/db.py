"""Database utilities: schema migration for the feedback store.

Usage:
    python -m contextmesh.db migrate [--database-url URL]

The database URL is resolved from --database-url, then
CONTEXTMESH_DATABASE_URL, then config.yaml's database.url.

Statements are applied one at a time; failures on optional features
(e.g. CREATE EXTENSION vector on servers without pgvector, or the
embedding column that depends on it) are logged and skipped so the
core tables always land.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from contextmesh.config import load_config

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "feedback" / "schema.sql"

# Statements allowed to fail without aborting the migration.
_OPTIONAL_MARKERS = ("CREATE EXTENSION", "VECTOR(")


def _split_statements(sql: str) -> list[str]:
    """Split schema SQL into statements on top-level semicolons."""
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(current).strip()
            if statement.rstrip(";").strip():
                statements.append(statement)
            current = []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def migrate(database_url: str) -> int:
    """Apply the feedback schema to a PostgreSQL database.

    Args:
        database_url: PostgreSQL connection URL.

    Returns:
        Number of statements applied successfully.

    Raises:
        RuntimeError: If psycopg2 is unavailable or connection fails.
    """
    try:
        import psycopg2
    except ImportError as e:
        raise RuntimeError(
            "psycopg2 is required for migrations: pip install psycopg2-binary"
        ) from e

    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = _split_statements(sql)

    try:
        conn = psycopg2.connect(database_url)
    except Exception as e:
        raise RuntimeError(f"Cannot connect to {database_url!r}: {e}") from e

    conn.autocommit = True
    applied = 0
    try:
        with conn.cursor() as cur:
            for statement in statements:
                try:
                    cur.execute(statement)
                    applied += 1
                except Exception as e:
                    upper = statement.upper()
                    if any(marker in upper for marker in _OPTIONAL_MARKERS):
                        logger.warning("Skipping optional statement (%s)", e)
                    else:
                        raise
    finally:
        conn.close()

    logger.info("Applied %d/%d schema statements", applied, len(statements))
    return applied


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(prog="python -m contextmesh.db")
    parser.add_argument("command", choices=["migrate"])
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    database_url = args.database_url or load_config().database_url
    if not database_url:
        print(
            "No database URL configured. Pass --database-url, set "
            "CONTEXTMESH_DATABASE_URL, or fill database.url in config.yaml.",
            file=sys.stderr,
        )
        return 2

    try:
        migrate(database_url)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
