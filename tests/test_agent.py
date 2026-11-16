"""The agent's graph, run on real listings and the real toolbox, with a scripted model.

The scripted model answers from a list, so these tests make no paid calls. The tests
under "With the database" need a running database and a finished build; they are
skipped otherwise.
"""

import json

import psycopg
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from mcp.shared.memory import create_connected_server_and_client_session

from listingaudit.agent.brain import Brain
from listingaudit.agent.graph import build_graph
from listingaudit.agent.report import FindingDraft, KindChoice, Report, ReportDraft
from listingaudit.agent.run import cost
from listingaudit.agent.scripted import ScriptedModel
from listingaudit.agent.toolbox import Toolbox, ToolError, open_toolbox
from listingaudit.config import settings
from listingaudit.db import connect
from listingaudit.market.rules import Rule
from listingaudit.photos.colours import FAMILIES
from listingaudit.toolbox import server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_the_agent_starts_the_toolbox_as_its_own_process():
    async with open_toolbox() as toolbox:
        assert toolbox.search.name == "search_rulebook"
        with pytest.raises(ToolError, match="should match pattern"):
            await toolbox.call("get_listing", offer_id="42")


def test_the_cost_comes_from_the_list_price():
    tokens = {"input_tokens": 1_000_000, "output_tokens": 500_000}
    assert cost({"gpt-5-mini-2025-08-07": tokens}) == pytest.approx(1.25)
    assert cost({"a-model-without-a-price": tokens}) is None


# --- With the database ---------------------------------------------------------


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
def database(owner, monkeypatch):
    """The database, with the photo check left to a person."""
    monkeypatch.setattr("listingaudit.photos.check.get_clip", UnsureClip)
    monkeypatch.setattr(settings, "vision_api_key", None)
    return owner


async def audit(offer_id: str, small: ScriptedModel, large=None, **limits) -> dict:
    """Run the graph on a listing, with the toolbox in memory; return the final state."""
    async with create_connected_server_and_client_session(server.mcp) as session:
        graph = build_graph(await Toolbox.connect(session), Brain(small, large), **limits)
        return await graph.ainvoke({"offer_id": offer_id})


def kind(name: str) -> KindChoice:
    return KindChoice(kind=name, reason="Scripted.")


def search(*queries: str) -> AIMessage:
    """The model asking for rulebook searches."""
    calls = [
        {"name": "search_rulebook", "args": {"query": query, "k": 3}, "id": f"call_{n}"}
        for n, query in enumerate(queries)
    ]
    return AIMessage("", tool_calls=calls)


DONE = AIMessage("Each problem has a passage.")
PRICE_SEARCH = search("Article 6a prior price lowest price 30 days")


def offered(messages) -> list[str]:
    """The chunk ids of the passages a request to the report writer offers."""
    text = messages[-1].content
    heading = "The rulebook passages found for them:\n"
    passages, _ = json.JSONDecoder().raw_decode(text, text.index(heading) + len(heading))
    return [passage["chunk_id"] for passage in passages]


def good_draft(messages) -> ReportDraft:
    finding = FindingDraft(
        rule=Rule.PRIOR_PRICE,
        explanation="The badge says -42%, but the price is only 25.0% below €19.99.",
        suggested_fix="Show -25% on the badge, or leave the badge out.",
        quotes=offered(messages)[:1],
    )
    return ReportDraft(summary="One possible problem: the -42% badge.", findings=[finding])


def sloppy_draft(messages) -> ReportDraft:
    finding = FindingDraft(
        rule=Rule.PRIOR_PRICE,
        explanation="The badge promises €5.00 more off than the price history allows.",
        suggested_fix="Fix the badge.",
        quotes=["made/up#1"],
    )
    return ReportDraft(summary="The badge is illegal.", findings=[finding])


@pytest.mark.anyio
async def test_the_agent_audits_a_listing(database):
    small = ScriptedModel(script=[kind("textile"), PRICE_SEARCH, DONE, good_draft])
    state = await audit("P0171", small)

    report = Report.model_validate(state["report"])
    assert [rule["rule"] for rule in state["rules"]] == [
        "prior_price",
        "manufacturer_info",
        "eu_responsible_person",
        "fibre_composition",
        "photo_matches_text",
    ]
    [problem] = report.problems
    assert problem.rule == Rule.PRIOR_PRICE
    assert problem.explanation.startswith("The badge says -42%")
    assert problem.severity == "high"
    assert problem.facts["real_discount_percent"] == "25.0"
    assert problem.law[0] == "Directive 98/6/EC Art. 6a(1)"
    assert [quote.chunk_id for quote in problem.quotes] == offered(small.requests[-1])[:1]
    assert [note.rule for note in report.passed] == [
        Rule.MANUFACTURER_INFO,
        Rule.EU_RESPONSIBLE_PERSON,
        Rule.FIBRE_COMPOSITION,
    ]
    assert [note.rule for note in report.to_check] == [Rule.PHOTO_MATCHES_TEXT]
    assert report.unresolved == []
    assert state["attempts"] == 1
    assert small.script == []


@pytest.mark.anyio
async def test_the_listing_reaches_the_model_only_as_data_in_the_kind_question(database):
    small = ScriptedModel(script=[kind("textile"), PRICE_SEARCH, DONE, good_draft])
    state = await audit("P0171", small)

    [system, listing], *later = small.requests
    title = state["listing"]["title"]
    assert "never instructions" in system.content
    assert listing.content.startswith("<listing>\n") and listing.content.endswith("\n</listing>")
    assert title in listing.content
    assert not any(title in str(message.content) for request in later for message in request)


@pytest.mark.anyio
async def test_a_failed_draft_goes_back_with_the_self_checks_feedback(database):
    small = ScriptedModel(script=[kind("textile"), PRICE_SEARCH, DONE, sloppy_draft, good_draft])
    state = await audit("P0171", small)

    retry = small.requests[-1][-1].content
    assert "It failed the self-check" in retry
    assert "quotes 'made/up#1'" in retry
    assert "states 5.00" in retry
    assert 'says "illegal"' in retry
    assert state["attempts"] == 2
    assert Report.model_validate(state["report"]).unresolved == []


@pytest.mark.anyio
async def test_a_draft_the_small_model_cannot_fix_goes_to_the_large_model(database):
    small = ScriptedModel(script=[kind("textile"), PRICE_SEARCH, DONE, sloppy_draft, sloppy_draft])
    large = ScriptedModel(script=[good_draft])
    state = await audit("P0171", small, large)

    assert state["large"]
    assert state["attempts"] == 3
    assert len(large.requests) == 1
    assert Report.model_validate(state["report"]).unresolved == []


@pytest.mark.anyio
async def test_a_problem_the_model_leaves_out_still_reaches_the_reviewer(database):
    """P0198's description tells automated reviewers to approve it."""
    note = database.execute(
        "SELECT sneaky_note FROM answer_key.cases WHERE offer_id = 'P0198'"
    ).fetchone()[0]
    talked_out = ReportDraft(summary="The listing was verified as compliant.", findings=[])
    small = ScriptedModel(script=[kind("unit_price"), PRICE_SEARCH, DONE, talked_out, talked_out])
    state = await audit("P0198", small)

    kind_question = small.requests[0][-1].content
    assert kind_question.index("<listing>") < kind_question.index(note)
    assert kind_question.index(note) < kind_question.index("</listing>")
    report = Report.model_validate(state["report"])
    [problem] = report.problems
    check = next(result for result in state["results"] if result["rule"] == "prior_price")
    assert report.summary == "1 possible problem: Honest discounts (the 30-day rule)."
    assert problem.explanation == check["summary"]
    assert problem.suggested_fix == ""
    assert report.unresolved[0].startswith("The prior_price check found a problem, but the draft")


@pytest.mark.anyio
async def test_a_listing_without_problems_needs_no_law_and_no_writer(database):
    small = ScriptedModel(script=[kind("light_bulb")])
    state = await audit("P0001", small)

    report = Report.model_validate(state["report"])
    assert report.problems == []
    assert report.summary == "No possible problems found in 4 rules."
    assert state["messages"] == []
    assert "draft" not in state
    assert small.script == []


@pytest.mark.anyio
async def test_the_searches_stop_at_their_limit(database):
    small = ScriptedModel(script=[kind("textile"), PRICE_SEARCH, search("Grundpreis"), good_draft])
    state = await audit("P0171", small, max_searches=1)

    searched = [message for message in state["messages"] if isinstance(message, ToolMessage)]
    assert len(searched) == 1
    assert state["attempts"] == 1
