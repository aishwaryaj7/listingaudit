"""Small helpers for talking to Postgres."""

import psycopg

from listingaudit.config import PROJECT_ROOT, settings

SCHEMA_FILE = PROJECT_ROOT / "db" / "schema.sql"


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database_url)


def connect_as_agent() -> psycopg.Connection:
    """Log in as the agent, who may read the marketplace but not the answer key."""
    return psycopg.connect(settings.agent_database_url)


def apply_schema(conn: psycopg.Connection) -> None:
    """Create the tables if they don't exist yet. Safe to run many times."""
    conn.execute(SCHEMA_FILE.read_text())
