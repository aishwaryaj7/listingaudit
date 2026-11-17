"""The review API: start audits, follow them as they run, and record a person's decisions.

    uv run uvicorn listingaudit.review.api:app

It serves http://127.0.0.1:8000, with interactive docs at /docs; the review screen is
its client. An audit runs until its report waits for review, and its steps stream as
server-sent events. A decision resumes the audit, which saves the review and a draft
message to the seller. Nothing is ever sent to a seller.
"""

import logging
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from listingaudit.agent.report import Review
from listingaudit.agent.toolbox import ToolError
from listingaudit.review.service import (
    AlreadyReviewed,
    Audit,
    AuditNotFound,
    LogLine,
    Progress,
    ReviewIncomplete,
    ReviewService,
    open_service,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with open_service() as service:
        app.state.service = service
        yield


app = FastAPI(
    title="ListingAudit review API",
    description="Audits wait here for a person's decision. Nothing is ever sent to a seller.",
    lifespan=lifespan,
)


def get_service(request: Request) -> ReviewService:
    return request.app.state.service


Service = Annotated[ReviewService, Depends(get_service)]
OfferId = Annotated[str, Path(pattern=r"^[PE]\d{4}$", description="The listing's id, e.g. P0171")]


async def find_listing(offer_id: OfferId, service: Service) -> dict[str, Any]:
    try:
        return await service.listing(offer_id)
    except ToolError as error:
        raise HTTPException(404, f"There is no listing {offer_id}.") from error


Listing = Annotated[dict[str, Any], Depends(find_listing)]


@app.get("/health")
async def health() -> dict[str, str]:
    """For the probes of a container platform: the API has started and serves."""
    return {"status": "ok"}


@app.get("/listings/{offer_id}")
async def read_listing(listing: Listing) -> dict[str, Any]:
    """A listing as shoppers see it."""
    return listing


@app.get(
    "/listings/{offer_id}/photo",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def read_photo(listing: Listing, service: Service) -> Response:
    """The listing's main photo."""
    return Response(await service.photo(listing["offer_id"]), media_type="image/jpeg")


@app.post("/listings/{offer_id}/audits", response_class=EventSourceResponse)
async def start_audit(listing: Listing, service: Service) -> AsyncIterable[Progress]:
    """Audit a listing. Each step is sent as it ends, until the report waits for review."""
    try:
        async for progress in service.audit(listing["offer_id"]):
            yield progress
    except Exception as error:
        # The response has started with status 200, so the stream has to say it failed
        logger.exception("The audit of %s failed", listing["offer_id"])
        yield ServerSentEvent(event="error", data={"detail": f"The audit failed: {error}"})


@app.get("/audits")
async def waiting_audits(service: Service) -> list[Audit]:
    """The audits waiting for review, oldest first."""
    return await service.waiting()


@app.get("/audits/{audit_id}")
async def read_audit(audit_id: UUID, service: Service) -> Audit:
    try:
        return await service.get(audit_id)
    except AuditNotFound as error:
        raise HTTPException(404, str(error)) from error


@app.post("/audits/{audit_id}/review")
async def review_audit(audit_id: UUID, review: Review, service: Service) -> Audit:
    """Approve or reject each possible problem of a report waiting for review.

    A review that leaves a finding open is refused with what it lacks, and the audit
    keeps waiting.
    """
    try:
        return await service.review(audit_id, review)
    except AuditNotFound as error:
        raise HTTPException(404, str(error)) from error
    except AlreadyReviewed as error:
        raise HTTPException(409, str(error)) from error
    except ReviewIncomplete as error:
        raise HTTPException(422, error.errors) from error


@app.get("/log")
async def read_log(
    service: Service, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> list[LogLine]:
    """The latest entries of the audit log, newest first."""
    return await service.history(limit)
