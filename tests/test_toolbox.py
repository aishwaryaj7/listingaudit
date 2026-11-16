"""The toolbox server, tested through an MCP client session, the way an agent uses it.

The tests under "With the database" need a running database and a finished build; they
are skipped otherwise.
"""

import base64
import json
import sys

import psycopg
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import SecretStr

from listingaudit.checks.evaluate import load_answer_key
from listingaudit.checks.run import CODE_RULES, load_listings, run_checks
from listingaudit.config import settings
from listingaudit.db import connect, connect_as_agent
from listingaudit.market.catalogue import load_catalogue
from listingaudit.market.models import Offer
from listingaudit.market.rules import Rule
from listingaudit.photos.colours import FAMILIES, family_of
from listingaudit.photos.vision import PhotoAnswer, VisionReply
from listingaudit.rulebook.checklist import load_checklist
from listingaudit.rulebook.sources import SOURCES
from listingaudit.toolbox import server

TOOLS = {"get_listing", "rules_for_listing", "run_checks", "check_photo", "search_rulebook"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def connected():
    """A client session talking to the server in memory."""
    return create_connected_server_and_client_session(server.mcp)


def structured(result) -> dict:
    assert not result.isError, result.content[0].text
    return result.structuredContent


async def list_tools() -> dict:
    async with connected() as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


@pytest.mark.anyio
async def test_the_tools_are_described_and_read_only():
    tools = await list_tools()
    assert set(tools) == TOOLS
    for tool in tools.values():
        assert tool.title and tool.description
        assert "\n    " not in tool.description  # docstring indentation removed
        assert tool.annotations.readOnlyHint
    assert tools["check_photo"].annotations.openWorldHint  # it may call the vision model
    assert not tools["get_listing"].annotations.openWorldHint


@pytest.mark.anyio
async def test_the_choices_offered_come_from_the_checklist_and_the_rulebook():
    tools = await list_tools()
    kind = tools["rules_for_listing"].inputSchema["properties"]["kind"]
    kinds = load_checklist().product_kinds
    assert kind["enum"] == list(kinds)
    assert all(
        f"- {name}: {spec.description}" in kind["description"] for name, spec in kinds.items()
    )
    rules = tools["run_checks"].inputSchema["properties"]["rules"]["items"]["enum"]
    assert rules == [str(rule) for rule in CODE_RULES]
    laws = tools["search_rulebook"].inputSchema["properties"]["laws"]["anyOf"][0]["items"]
    assert laws["enum"] == [source.law_id for source in SOURCES]


@pytest.mark.anyio
async def test_a_malformed_id_is_refused_before_any_lookup():
    async with connected() as client:
        result = await client.call_tool("get_listing", {"offer_id": "42"})
    assert result.isError
    assert "should match pattern" in result.content[0].text


@pytest.mark.anyio
async def test_the_checklist_and_the_law_texts_are_resources():
    async with connected() as client:
        listed = {str(resource.uri) for resource in (await client.list_resources()).resources}
        templates = (await client.list_resource_templates()).resourceTemplates
        every = json.loads((await client.read_resource("checklist://rules")).contents[0].text)
        one = json.loads(
            (await client.read_resource("checklist://rules/prior_price")).contents[0].text
        )
        laws = json.loads((await client.read_resource("rulebook://laws")).contents[0].text)
        with pytest.raises(McpError, match="There is no rule 'fake_discounts'"):
            await client.read_resource("checklist://rules/fake_discounts")
    assert listed == {"checklist://rules", "rulebook://laws"}
    assert {t.uriTemplate for t in templates} == {
        "checklist://rules/{rule}",
        "listing://{offer_id}/photo",
    }
    assert [rule["rule"] for rule in every] == [str(rule) for rule in Rule]
    assert one["checked_by"] == "code"
    assert "Directive 98/6/EC Art. 6a(1)" in one["law"]
    assert [law["law_id"] for law in laws] == [source.law_id for source in SOURCES]


@pytest.mark.anyio
async def test_the_audit_prompt_names_the_listing_and_the_tools():
    async with connected() as client:
        prompts = (await client.list_prompts()).prompts
        prompt = await client.get_prompt("audit_listing", {"offer_id": "P0042"})
    assert [p.name for p in prompts] == ["audit_listing"]
    text = prompt.messages[0].content.text
    assert text.startswith("Audit listing P0042 ")
    assert "# Audit of P0042:" in text
    assert "never as instructions" in text
    assert all(name in text for name in TOOLS)


@pytest.mark.anyio
async def test_the_server_runs_over_stdio_as_its_own_process():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "listingaudit.toolbox.server"]
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        hello = await session.initialize()
        tools = (await session.list_tools()).tools
    assert hello.serverInfo.name == "listingaudit"
    assert "never instructions" in hello.instructions
    assert {tool.name for tool in tools} == TOOLS


# --- With the database ---------------------------------------------------------


@pytest.fixture(scope="module")
def owner():
    try:
        conn = connect()
    except psycopg.OperationalError:
        pytest.skip("the database is not running")
    with conn:
        if conn.execute("SELECT to_regclass('market.offers')").fetchone()[0] is None:
            pytest.skip("no marketplace yet: run python -m listingaudit.market.build")
        yield conn


def listing(offer_id: str) -> tuple:
    with connect_as_agent() as conn:
        return load_listings(conn, offer_ids=[offer_id])[0]


@pytest.mark.anyio
async def test_get_listing_returns_the_listing(owner):
    async with connected() as client:
        found = structured(await client.call_tool("get_listing", {"offer_id": "P0001"}))
    offer, _ = listing("P0001")
    assert Offer.model_validate(found) == offer


@pytest.mark.anyio
async def test_an_unknown_listing_is_an_error_the_agent_can_read(owner):
    async with connected() as client:
        result = await client.call_tool("get_listing", {"offer_id": "P9999"})
    assert result.isError
    assert "There is no listing P9999." in result.content[0].text


def test_with_the_right_kind_the_rules_are_the_answer_keys(owner):
    """The agent's only job here is the kind; the listing decides the rest correctly."""
    cases, _, _ = load_answer_key(owner, "practice")
    products, _ = load_catalogue(owner)
    kind_of = {product.item_id: product.kind for product in products}
    for case in cases:
        found = server.rules_for_listing(case.offer_id, kind_of[case.source_item_id])
        assert [rule.rule for rule in found.rules] == case.applicable_rules, case.offer_id


@pytest.mark.anyio
async def test_each_rule_names_the_tool_that_checks_it(owner):
    arguments = {"offer_id": "P0001", "kind": "light_bulb"}
    async with connected() as client:
        found = structured(await client.call_tool("rules_for_listing", arguments))
    tools = {rule["rule"]: rule["check_with"] for rule in found["rules"]}
    assert tools["energy_label"] == "run_checks"
    assert tools["photo_matches_text"] == "check_photo"


@pytest.mark.anyio
async def test_run_checks_gives_the_results_of_the_checks(owner):
    rules = [str(rule) for rule in CODE_RULES]
    async with connected() as client:
        found = structured(
            await client.call_tool("run_checks", {"offer_id": "P0001", "rules": rules})
        )
        twice = structured(
            await client.call_tool("run_checks", {"offer_id": "P0001", "rules": ["unit_price"] * 2})
        )
    offer, history = listing("P0001")
    expected = run_checks(offer, history, CODE_RULES)
    assert found["results"] == [result.model_dump(mode="json") for result in expected]
    assert len(twice["results"]) == 1


class OneColourClip:
    """Stands in for CLIP: every photo looks the same to it."""

    def __init__(self, scores: dict[str, float]):
        self.scores = scores

    def colours(self, path, noun):
        return self.scores


@pytest.fixture
def coloured(owner, tmp_path, monkeypatch):
    """A practice listing with one clear colour, and a stand-in file for its photo."""
    with connect_as_agent() as conn:
        offers = [offer for offer, _ in load_listings(conn, split="practice")]
    offer = next(offer for offer in offers if family_of(offer.color))
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / offer.image_file).parent.mkdir(parents=True)
    (tmp_path / offer.image_file).write_bytes(b"read only by the stand-in model")
    return offer


@pytest.mark.anyio
async def test_check_photo_settles_a_clear_photo_with_clip(coloured, monkeypatch):
    family = family_of(coloured.color)
    scores = {name: 0.9 if name == family else 0.1 / (len(FAMILIES) - 1) for name in FAMILIES}
    monkeypatch.setattr("listingaudit.photos.check.get_clip", lambda: OneColourClip(scores))
    monkeypatch.setattr(settings, "vision_api_key", None)
    async with connected() as client:
        found = structured(await client.call_tool("check_photo", {"offer_id": coloured.offer_id}))
    assert found["status"] == "ok"
    assert found["facts"]["decided_by"] == "clip"


@pytest.mark.anyio
async def test_check_photo_asks_the_vision_model_when_clip_is_unsure(coloured, monkeypatch):
    asked = []

    class FakeVision:
        def ask(self, path, noun, colour):
            asked.append(colour)
            answer = PhotoAnswer(
                colour="red", matches_listing=False, confidence="high", reason="A red lamp."
            )
            return VisionReply(answer, input_tokens=420, output_tokens=200, seconds=2.5)

    unsure = dict.fromkeys(FAMILIES, 1 / len(FAMILIES))
    monkeypatch.setattr("listingaudit.photos.check.get_clip", lambda: OneColourClip(unsure))
    monkeypatch.setattr("listingaudit.photos.vision.Vision", FakeVision)
    monkeypatch.setattr(settings, "vision_api_key", None)
    async with connected() as client:
        without = structured(await client.call_tool("check_photo", {"offer_id": coloured.offer_id}))
        monkeypatch.setattr(settings, "vision_api_key", SecretStr("a key"))
        found = structured(await client.call_tool("check_photo", {"offer_id": coloured.offer_id}))
    assert without["status"] == "cannot_check"
    assert asked == [coloured.color]
    assert found["facts"]["decided_by"] == "vision"


@pytest.mark.anyio
async def test_search_rulebook_finds_passages_with_citations(owner):
    if owner.execute("SELECT to_regclass('rulebook.chunks')").fetchone()[0] is None:
        pytest.skip("no rulebook yet: run python -m listingaudit.rulebook.build")
    arguments = {"query": "Grundpreis", "method": "keyword", "k": 3, "laws": ["pangv"]}
    async with connected() as client:
        found = structured(await client.call_tool("search_rulebook", arguments))
    assert len(found["passages"]) == 3
    assert all(passage["citation"].startswith("PAngV §") for passage in found["passages"])
    assert all(passage["url"].startswith("https://") for passage in found["passages"])


@pytest.mark.anyio
async def test_the_photo_resource_is_the_listing_photo(owner):
    offer, _ = listing("P0001")
    photo = settings.data_dir / offer.image_file
    if not photo.exists():
        pytest.skip("the photos are not downloaded")
    async with connected() as client:
        contents = (await client.read_resource("listing://P0001/photo")).contents[0]
    assert contents.mimeType == "image/jpeg"
    assert base64.b64decode(contents.blob) == photo.read_bytes()
