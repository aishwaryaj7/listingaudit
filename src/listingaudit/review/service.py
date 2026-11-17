"""The review service: audits that wait for a person's decision, and their audit log.

An audit runs the agent's graph with LangGraph's Postgres checkpointer, so it pauses
after the report until a person decides, and the pause outlives the process: after a
restart, the service resumes the same audits. Each report and each decision also goes
to the audit log. The review API serves this module; nothing in it contacts a seller.
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from listingaudit.agent.brain import Brain
from listingaudit.agent.graph import build_graph, step_line
from listingaudit.agent.report import Report, Review, SellerMessage, counted
from listingaudit.agent.toolbox import Toolbox, open_toolbox
from listingaudit.config import settings
from listingaudit.review.log import AuditLog, Entry, Event, open_pool


class AuditNotFound(LookupError):
    pass


class AlreadyReviewed(Exception):
    pass


class ReviewIncomplete(ValueError):
    """The review leaves something open, so the audit keeps waiting."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ".join(errors))
        self.errors = errors


class Progress(BaseModel):
    """What a step of an audit did, sent as the step ends."""

    audit_id: UUID
    step: str = Field(
        description='A step of the graph, or "waiting" once the report waits for review'
    )
    detail: str
    seconds: float = Field(description="Time since the audit started")


class Audit(BaseModel):
    """An audit as the reviewer sees it: the report and, once decided, the review."""

    audit_id: UUID
    offer_id: str
    status: Literal["waiting", "reviewed"]
    reported_at: datetime
    report: Report
    review: Review | None = None
    reviewed_at: datetime | None = None
    flagged: bool = Field(default=False, description="Whether a person approved a finding")
    message: SellerMessage | None = None


class LogLine(BaseModel):
    """An entry of the audit log, in one line."""

    at: datetime
    audit_id: UUID
    offer_id: str
    event: Event
    detail: str


def as_audit(reported: Entry, reviewed: Entry | None = None) -> Audit:
    review = reviewed.data["review"] if reviewed else None
    return Audit(
        audit_id=reported.audit_id,
        offer_id=reported.offer_id,
        status="reviewed" if reviewed else "waiting",
        reported_at=reported.at,
        report=reported.data["report"],
        review=review,
        reviewed_at=reviewed.at if reviewed else None,
        flagged=bool(review) and any(decision["approved"] for decision in review["decisions"]),
        message=reviewed.data["message"] if reviewed else None,
    )


def log_line(entry: Entry) -> LogLine:
    if entry.event == "reported":
        detail = entry.data["report"]["summary"]
    else:
        review = Review.model_validate(entry.data["review"])
        approved = sum(decision.approved for decision in review.decisions)
        if review.decisions:
            detail = f"{review.reviewer} approved {approved} of "
            detail += counted(len(review.decisions), "finding")
        else:
            detail = f"{review.reviewer} confirmed a report without possible problems"
        detail += "; message to the seller drafted" if entry.data["message"] else ""
    return LogLine(
        at=entry.at,
        audit_id=entry.audit_id,
        offer_id=entry.offer_id,
        event=entry.event,
        detail=detail,
    )


def thread(audit_id: UUID) -> RunnableConfig:
    """The graph's config for an audit: each audit is a thread of the checkpointer."""
    return {"configurable": {"thread_id": str(audit_id)}}


@dataclass
class ReviewService:
    toolbox: Toolbox
    graph: CompiledStateGraph
    log: AuditLog

    @classmethod
    async def create(
        cls, pool: AsyncConnectionPool, toolbox: Toolbox, brain: Brain
    ) -> "ReviewService":
        """Set up the review tables, which is safe to repeat, and build the agent."""
        log = AuditLog(pool)
        await log.setup()
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        return cls(toolbox, build_graph(toolbox, brain, checkpointer=checkpointer), log)

    async def listing(self, offer_id: str) -> dict[str, Any]:
        return await self.toolbox.call("get_listing", offer_id=offer_id)

    async def photo(self, offer_id: str) -> bytes:
        return await self.toolbox.photo(offer_id)

    async def audit(self, offer_id: str) -> AsyncIterator[Progress]:
        """Audit a listing, giving each step as it ends, until the report waits for review."""
        audit_id = uuid4()
        usage = UsageMetadataCallbackHandler()
        config = thread(audit_id) | {"callbacks": [usage]}
        started = time.perf_counter()

        def progress(step: str, detail: str) -> Progress:
            seconds = round(time.perf_counter() - started, 1)
            return Progress(audit_id=audit_id, step=step, detail=detail, seconds=seconds)

        # Saved when the graph pauses or ends, not after every step: the pause is what has
        # to survive, and an audit cut off halfway is cheaper to run again
        stream = self.graph.astream(
            {"offer_id": offer_id}, config, stream_mode="updates", durability="exit"
        )
        async for chunk in stream:
            for step, update in chunk.items():
                if step != "__interrupt__":
                    yield progress(step, step_line(step, update))

        state = (await self.graph.aget_state(config)).values
        record = {
            "report": state["report"],
            "kind_reason": state["kind_reason"],
            "searches": [
                call["args"] for m in state["messages"] for call in getattr(m, "tool_calls", [])
            ],
            "drafts": state.get("attempts", 0),
            "large": state.get("large", False),
            "seconds": round(time.perf_counter() - started, 1),
            "tokens": {
                model: {key: data[key] for key in ("input_tokens", "output_tokens")}
                for model, data in usage.usage_metadata.items()
            },
        }
        await self.log.add(audit_id, offer_id, "reported", record)
        yield progress("waiting", state["report"]["summary"])

    async def waiting(self) -> list[Audit]:
        """The audits waiting for review, oldest first."""
        return [as_audit(entry) for entry in await self.log.waiting()]

    async def get(self, audit_id: UUID) -> Audit:
        events = await self.log.events(audit_id)
        if "reported" not in events:
            raise AuditNotFound(f"There is no audit {audit_id}.")
        return as_audit(events["reported"], events.get("reviewed"))

    async def review(self, audit_id: UUID, review: Review) -> Audit:
        """Record a person's decision: the audit resumes and drafts the seller message."""
        audit = await self.get(audit_id)
        if audit.status == "reviewed":
            raise AlreadyReviewed(f"Audit {audit_id} has been reviewed already.")
        resume = Command(resume=review.model_dump(mode="json"))
        state = await self.graph.ainvoke(resume, thread(audit_id), durability="exit")
        if "__interrupt__" in state:
            raise ReviewIncomplete(state["__interrupt__"][0].value["errors"])
        record = {"review": state["review"], "message": state["message"]}
        try:
            await self.log.add(audit_id, audit.offer_id, "reviewed", record)
        except UniqueViolation as error:
            raise AlreadyReviewed(f"Audit {audit_id} has been reviewed already.") from error
        return await self.get(audit_id)

    async def history(self, limit: int = 50) -> list[LogLine]:
        """The latest entries of the audit log, newest first."""
        return [log_line(entry) for entry in await self.log.latest(limit)]


@asynccontextmanager
async def open_service(
    brain: Brain | None = None, schema: str | None = None
) -> AsyncIterator[ReviewService]:
    """The review service, with the toolbox started and the review tables set up."""
    brain = brain or Brain.from_settings()
    async with open_pool(schema or settings.review_schema) as pool, open_toolbox() as toolbox:
        yield await ReviewService.create(pool, toolbox, brain)
