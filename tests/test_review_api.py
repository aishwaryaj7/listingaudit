"""The review API over HTTP, with the review service on a scripted model.

The requests go straight to the app in memory, without a server. Each test works in a
review schema of its own, dropped at the end. The tests need a running database and a
finished build; they are skipped otherwise.
"""

import json
from uuid import uuid4

import httpx
import psycopg
import pytest
from langchain_core.messages import AIMessage
from mcp.shared.memory import create_connected_server_and_client_session
from psycopg import sql

from listingaudit.agent.brain import Brain
from listingaudit.agent.report import FindingDraft, KindChoice, ReportDraft
from listingaudit.agent.scripted import ScriptedModel
from listingaudit.agent.toolbox import Toolbox
from listingaudit.config import settings
from listingaudit.db import connect
from listingaudit.market.rules import Rule
from listingaudit.photos.colours import FAMILIES
from listingaudit.review.api import app
from listingaudit.review.log import open_pool
from listingaudit.review.service import ReviewService
from listingaudit.toolbox import server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def owner():
    try:
        conn = connect()
    except psycopg.OperationalError:
        pytest.skip("the database is not running")
    with conn:
        if conn.execute("SELECT to_regclass('rulebook.chunks')").fetchone()[0] is None:
            pytest.skip("no rulebook yet: run python -m listingaudit.rulebook.build")
        yield conn


class UnsureClip:
    """Stands in for CLIP: no photo looks like one colour more than another."""

    def colours(self, path, noun):
        return dict.fromkeys(FAMILIES, 1 / len(FAMILIES))


@pytest.fixture
def schema(owner, monkeypatch):
    """A review schema of the test's own, with the photo check left to a person."""
    monkeypatch.setattr("listingaudit.photos.check.get_clip", UnsureClip)
    monkeypatch.setattr(settings, "vision_api_key", None)
    name = f"review_test_{uuid4().hex[:8]}"
    yield name
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


def p0171() -> ScriptedModel:
    """The model's answers for P0171's badge: the kind, no searches, a draft that passes."""
    finding = FindingDraft(
        rule=Rule.PRIOR_PRICE,
        explanation="The badge says -42%, but the price is only 25.0% below €19.99.",
        suggested_fix="Show -25% on the badge, or leave the badge out.",
        quotes=[],
    )
    draft = ReportDraft(summary="One possible problem: the -42% badge.", findings=[finding])
    return ScriptedModel(
        script=[KindChoice(kind="textile", reason="Scripted."), AIMessage("No search."), draft]
    )


async def client_for(schema: str, model: ScriptedModel):
    """An HTTP client of the app, whose service runs on the scripted model."""
    async with (
        open_pool(schema) as pool,
        create_connected_server_and_client_session(server.mcp) as session,
    ):
        app.state.service = await ReviewService.create(
            pool, await Toolbox.connect(session), Brain(model)
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def client(schema):
    async for client in client_for(schema, p0171()):
        yield client


def events(response: httpx.Response) -> list[tuple[str, dict]]:
    """The server-sent events of a response, as (event, data)."""
    found = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        found.append((fields.get("event", "message"), json.loads(fields["data"])))
    return found


@pytest.mark.anyio
async def test_the_api_serves_listings_and_their_photos(client):
    listing = (await client.get("/listings/P0171")).json()
    photo = await client.get("/listings/P0171/photo")

    assert listing["offer_id"] == "P0171"
    assert listing["discount_percent"] == 42
    assert photo.headers["content-type"] == "image/jpeg"
    assert photo.content.startswith(b"\xff\xd8")  # every JPEG file starts so
    assert (await client.get("/listings/P9999")).json() == {"detail": "There is no listing P9999."}
    assert (await client.get("/listings/42")).status_code == 422
    assert (await client.get("/health")).json() == {"status": "ok"}


@pytest.mark.anyio
async def test_an_audit_streams_its_steps_until_it_waits_for_review(client):
    response = await client.post("/listings/P0171/audits")

    assert response.headers["content-type"].startswith("text/event-stream")
    sent = events(response)
    assert {event for event, _ in sent} == {"message"}
    assert [data["step"] for _, data in sent][-2:] == ["build_report", "waiting"]
    audit_id = sent[-1][1]["audit_id"]
    [waiting] = (await client.get("/audits")).json()
    assert waiting["audit_id"] == audit_id
    assert waiting["status"] == "waiting"
    assert (await client.post("/listings/P9999/audits")).status_code == 404


@pytest.mark.anyio
async def test_a_review_saves_the_decision_and_a_draft_message(client):
    audit_id = events(await client.post("/listings/P0171/audits"))[-1][1]["audit_id"]
    review = f"/audits/{audit_id}/review"

    undecided = await client.post(review, json={"reviewer": "Test Reviewer"})
    assert undecided.status_code == 422
    assert undecided.json()["detail"] == [
        "Approve or reject the finding on Honest discounts (the 30-day rule)."
    ]
    nameless = await client.post(review, json={"reviewer": "", "decisions": []})
    assert nameless.json()["detail"][0]["loc"] == ["body", "reviewer"]

    decision = {"rule": "prior_price", "approved": True}
    saved = await client.post(review, json={"reviewer": "Test Reviewer", "decisions": [decision]})
    audit = saved.json()
    assert saved.status_code == 200
    assert audit["status"] == "reviewed"
    assert audit["flagged"] is True
    assert (
        audit["message"]["seller_id"] == (await client.get("/listings/P0171")).json()["seller_id"]
    )

    again = await client.post(review, json={"reviewer": "Test Reviewer", "decisions": [decision]})
    assert again.status_code == 409
    assert (await client.get("/audits")).json() == []
    assert [line["event"] for line in (await client.get("/log")).json()] == [
        "reviewed",
        "reported",
    ]
    assert (await client.get(f"/audits/{uuid4()}")).status_code == 404


@pytest.mark.anyio
async def test_a_failed_audit_ends_its_stream_with_an_error(schema):
    # A model with nothing to say makes the audit fail after the listing is read
    async for client in client_for(schema, ScriptedModel(script=[])):
        response = await client.post("/listings/P0171/audits")
        assert response.status_code == 200
        [*steps, (event, data)] = events(response)
        assert [data["step"] for _, data in steps] == ["read_listing"]
        assert event == "error"
        assert data == {"detail": "The audit failed: The script has no answers left."}
        assert (await client.get("/audits")).json() == []
