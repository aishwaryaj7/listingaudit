"""The audit log: every report the agent writes, and every decision a person makes on one.

The log is the lasting, readable record of the reviews. Rows are only ever added: the
database refuses to change or delete them (db/review.sql). The log lives in the review
schema, next to the checkpoints of the audits waiting for review.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from listingaudit.config import PROJECT_ROOT, settings

SQL_FILE = PROJECT_ROOT / "db" / "review.sql"

Event = Literal["reported", "reviewed"]


class Entry(BaseModel):
    id: int
    at: datetime
    audit_id: UUID
    offer_id: str
    event: Event
    data: dict[str, Any]


@asynccontextmanager
async def open_pool(schema: str) -> AsyncIterator[AsyncConnectionPool]:
    """Database connections that work in the review schema, which is created if need be."""
    async with await AsyncConnection.connect(settings.database_url, autocommit=True) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    # The first three are the settings LangGraph's Postgres checkpointer needs
    kwargs = {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": f"-c search_path={schema}",
    }
    async with AsyncConnectionPool(settings.database_url, kwargs=kwargs, open=False) as pool:
        yield pool


class AuditLog:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def setup(self) -> None:
        """Create the log's table and its guards, unless they exist."""
        async with self.pool.connection() as conn:
            # A script of several statements can't run as a prepared statement
            await conn.execute(SQL_FILE.read_text(), prepare=False)

    async def add(self, audit_id: UUID, offer_id: str, event: Event, data: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO audit_log (audit_id, offer_id, event, data) VALUES (%s, %s, %s, %s)",
                (audit_id, offer_id, event, Jsonb(data)),
            )

    async def events(self, audit_id: UUID) -> dict[Event, Entry]:
        """The entries of one audit, by event."""
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM audit_log WHERE audit_id = %s ORDER BY id", (audit_id,)
            )
            rows = await cursor.fetchall()
        return {row["event"]: Entry.model_validate(row) for row in rows}

    async def waiting(self) -> list[Entry]:
        """The reports no one has reviewed yet, oldest first."""
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM audit_log AS report
                WHERE event = 'reported' AND NOT EXISTS (
                    SELECT 1 FROM audit_log AS review
                    WHERE review.audit_id = report.audit_id AND review.event = 'reviewed'
                )
                ORDER BY id
                """
            )
            rows = await cursor.fetchall()
        return [Entry.model_validate(row) for row in rows]

    async def latest(self, limit: int = 50) -> list[Entry]:
        """The latest entries, newest first."""
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT %s", (limit,)
            )
            rows = await cursor.fetchall()
        return [Entry.model_validate(row) for row in rows]
