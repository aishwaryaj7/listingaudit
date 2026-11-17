"""The review service, on the real toolbox and database, with a scripted model.

The scripted model makes the tests free. Each test works in a review schema of its own,
dropped at the end, so the tests never touch the real review queue. The tests need a
running database and a finished build; they are skipped otherwise.
"""

from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
from langchain_core.messages import AIMessage
from mcp.shared.memory import create_connected_server_and_client_session
from psycopg import sql

from listingaudit.agent.brain import Brain
from listingaudit.agent.report import Decision, FindingDraft, KindChoice, ReportDraft, Review
from listingaudit.agent.scripted import ScriptedModel
from listingaudit.agent.toolbox import Toolbox
from listingaudit.config import settings
from listingaudit.db import connect
from listingaudit.market.rules import Rule
from listingaudit.photos.colours import FAMILIES
from listingaudit.review.log import open_pool
from listingaudit.review.service import (
    AlreadyReviewed,
    AuditNotFound,
    ReviewIncomplete,
    ReviewService,
)
from listingaudit.toolbox import server

EXPLANATION = "The badge says -42%, but the price is only 25.0% below €19.99."


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


def p0171(audits: int = 1) -> ScriptedModel:
    """The model's answers for P0171's badge: the kind, no searches, a draft that passes."""
    finding = FindingDraft(
        rule=Rule.PRIOR_PRICE,
        explanation=EXPLANATION,
        suggested_fix="Show -25% on the badge, or leave the badge out.",
        quotes=[],
    )
    draft = ReportDraft(summary="One possible problem: the -42% badge.", findings=[finding])
    answers = [KindChoice(kind="textile", reason="Scripted."), AIMessage("No search."), draft]
    return ScriptedModel(script=answers * audits)


@asynccontextmanager
async def open_test_service(schema: str, model: ScriptedModel):
    async with (
        open_pool(schema) as pool,
        create_connected_server_and_client_session(server.mcp) as session,
    ):
        yield await ReviewService.create(pool, await Toolbox.connect(session), Brain(model))


def approve(fix: str = "") -> Review:
    return Review(
        reviewer="Test Reviewer",
        decisions=[Decision(rule=Rule.PRIOR_PRICE, approved=True, fix=fix)],
    )


@pytest.mark.anyio
async def test_an_audit_waits_for_review_and_is_logged(schema):
    async with open_test_service(schema, p0171()) as service:
        steps = [progress async for progress in service.audit("P0171")]
        [audit] = await service.waiting()

    assert [progress.step for progress in steps] == [
        "read_listing",
        "pick_rules",
        "run_checks",
        "look_up_law",
        "write_report",
        "self_check",
        "build_report",
        "waiting",
    ]
    assert {progress.audit_id for progress in steps} == {audit.audit_id}
    assert steps[-1].detail == "One possible problem: the -42% badge."
    assert audit.status == "waiting"
    assert not audit.flagged
    assert [problem.rule for problem in audit.report.problems] == [Rule.PRIOR_PRICE]


@pytest.mark.anyio
async def test_a_review_drafts_the_seller_message_from_the_approved_findings(schema):
    async with open_test_service(schema, p0171()) as service:
        [*_, waiting] = [progress async for progress in service.audit("P0171")]
        audit = await service.review(waiting.audit_id, approve(fix="Show -25% on the badge."))
        [reviewed, reported] = await service.history()

    assert audit.status == "reviewed"
    assert audit.flagged
    assert audit.message.subject == "Your listing P0171: 1 possible problem to look at"
    assert EXPLANATION in audit.message.body
    assert "What to change: Show -25% on the badge." in audit.message.body
    assert audit.message.body.endswith("Kind regards,\nTest Reviewer")
    assert reviewed.event == "reviewed"
    assert reviewed.detail == "Test Reviewer approved 1 of 1 finding; message to the seller drafted"
    assert reported.detail == "One possible problem: the -42% badge."


@pytest.mark.anyio
async def test_an_incomplete_review_is_refused_and_the_audit_keeps_waiting(schema):
    async with open_test_service(schema, p0171()) as service:
        [*_, waiting] = [progress async for progress in service.audit("P0171")]
        undecided = Review(reviewer="Test Reviewer")
        with pytest.raises(ReviewIncomplete) as refused:
            await service.review(waiting.audit_id, undecided)
        assert refused.value.errors == [
            "Approve or reject the finding on Honest discounts (the 30-day rule)."
        ]
        assert [audit.audit_id for audit in await service.waiting()] == [waiting.audit_id]

        rejected = Decision(rule=Rule.PRIOR_PRICE, approved=False, reason="The badge is fine.")
        review = Review(reviewer="Test Reviewer", decisions=[rejected])
        audit = await service.review(waiting.audit_id, review)

    assert audit.status == "reviewed"
    assert not audit.flagged
    assert audit.message is None
    assert audit.review.decisions[0].reason == "The badge is fine."


@pytest.mark.anyio
async def test_a_paused_audit_survives_a_restart(schema):
    async with open_test_service(schema, p0171()) as service:
        [*_, waiting] = [progress async for progress in service.audit("P0171")]

    # A new service, with new connections and an empty model script, finds the audit
    # where it paused: the checkpoints in Postgres hold the whole state of the graph
    async with open_test_service(schema, ScriptedModel(script=[])) as restarted:
        assert [audit.audit_id for audit in await restarted.waiting()] == [waiting.audit_id]
        audit = await restarted.review(waiting.audit_id, approve())

    assert audit.flagged
    assert "What to change: Show -25% on the badge, or leave the badge out." in audit.message.body


@pytest.mark.anyio
async def test_a_report_is_reviewed_only_once(schema):
    async with open_test_service(schema, p0171()) as service:
        [*_, waiting] = [progress async for progress in service.audit("P0171")]
        await service.review(waiting.audit_id, approve())
        with pytest.raises(AlreadyReviewed):
            await service.review(waiting.audit_id, approve())
        with pytest.raises(AuditNotFound):
            await service.get(uuid4())
        assert await service.waiting() == []


@pytest.mark.anyio
async def test_the_audit_log_only_grows(schema):
    async with open_test_service(schema, p0171()) as service:
        [progress async for progress in service.audit("P0171")]

    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        log = sql.SQL("{}.audit_log").format(sql.Identifier(schema))
        for change in ("UPDATE {} SET offer_id = 'P0001'", "DELETE FROM {}", "TRUNCATE {}"):
            with pytest.raises(psycopg.errors.RaiseException, match="only takes new rows"):
                conn.execute(sql.SQL(change).format(log))
        assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(log)).fetchone()[0] == 1
