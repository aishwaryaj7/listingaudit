"""Postgres connection helpers."""

import psycopg

from listingaudit.config import PROJECT_ROOT, settings

SCHEMA_FILE = PROJECT_ROOT / "db" / "schema.sql"


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_url)


def connect_as_agent() -> psycopg.Connection:
    """Connect with the agent's read-only role."""
    return psycopg.connect(settings.agent_database_url)


def apply_schema(conn: psycopg.Connection) -> None:
    """Create the catalogue tables if they don't exist."""
    conn.execute(SCHEMA_FILE.read_text())
