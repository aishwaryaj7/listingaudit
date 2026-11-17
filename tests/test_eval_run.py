"""The evaluation runner: which audits run, what each record keeps, and carrying on.

The models are scripted, so nothing here is paid for. The tests under "With the
database" need a running database and a finished build; they are skipped otherwise.
"""

import json

import psycopg
import pytest
from langchain_core.messages import AIMessage
from mcp.shared.memory import create_connected_server_and_client_session

from listingaudit.agent.brain import Brain
from listingaudit.agent.graph import build_graph
from listingaudit.agent.report import FindingDraft, KindChoice, ReportDraft
from listingaudit.agent.scripted import ScriptedModel
from listingaudit.agent.toolbox import Toolbox
from listingaudit.checks.run import load_listings
from listingaudit.config import settings
from listingaudit.db import connect
from listingaudit.eval import records, run
from listingaudit.eval.baselines import OnePrompt, OnePromptAnswer, OnePromptFinding
from listingaudit.eval.records import read_records
from listingaudit.market.rules import Rule
from listingaudit.photos.colours import FAMILIES
from listingaudit.toolbox import server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def listings(make_offer):
    return [(make_offer(offer_id=f"E000{n}"), []) for n in range(1, 6)]


def test_each_listing_runs_once_per_repeat_until_it_has_a_record(listings):
    todo = run.jobs(listings, done={("E0001", 0)}, repeats=2, sample=None)
    assert [(listing[0].offer_id, repeat) for listing, repeat in todo] == [
        ("E0001", 1),
        ("E0002", 0),
        ("E0002", 1),
        ("E0003", 0),
        ("E0003", 1),
        ("E0004", 0),
        ("E0004", 1),
        ("E0005", 0),
        ("E0005", 1),
    ]


def test_a_sample_is_the_same_listings_every_time(listings):
    first = run.jobs(listings, done=set(), repeats=1, sample=2)
    again = run.jobs(listings, done=set(), repeats=1, sample=2)
    assert len(first) == 2
    assert [listing[0].offer_id for listing, _ in first] == [
        listing[0].offer_id for listing, _ in again
    ]


@pytest.mark.anyio
async def test_a_run_keeps_failed_audits_and_stops_after_too_many_in_a_row(listings, tmp_path):
    path = tmp_path / "agent.jsonl.gz"
    six = listings + [(listings[0][0].model_copy(update={"offer_id": "E0006"}), [])]

    async def fails(offer, history, repeat):
        raise RuntimeError("The quota is used up.")

    finished = await run.run_jobs("agent", [(listing, 0) for listing in six], fails, path, 1)
    assert len(finished) == run.MAX_FAILURES_IN_A_ROW == 5
    saved = read_records(path)
    assert [record.offer_id for record in saved] == ["E0001", "E0002", "E0003", "E0004", "E0005"]
    assert {record.error for record in saved} == {"RuntimeError: The quota is used up."}


@pytest.mark.anyio
async def test_rules_only_records_its_kind_rules_results_and_report(make_offer):
    rod = make_offer(offer_id="E0001", product_type="CURTAIN", title="Curtain Rod")
    record = await run.rules_only_audit(rod, [], 0)
    assert (record.system, record.kind, record.cost) == ("rules_only", "textile", 0.0)
    assert record.rules == [
        Rule.MANUFACTURER_INFO,
        Rule.FIBRE_COMPOSITION,
        Rule.PHOTO_MATCHES_TEXT,
    ]
    assert [result["rule"] for result in record.results] == record.rules
    assert [problem.rule for problem in record.report.problems] == [Rule.FIBRE_COMPOSITION]


@pytest.mark.anyio
async def test_one_prompt_records_its_report_and_the_checks_it_never_saw(make_offer):
    offer = make_offer(offer_id="E0001", manufacturer_address=None)
    finding = OnePromptFinding(
        rule=Rule.MANUFACTURER_INFO,
        explanation="The listing gives no postal address for the manufacturer.",
        suggested_fix="Add the manufacturer's postal address.",
        law="gpsr",
        article="19",
    )
    answer = OnePromptAnswer(summary="One possible problem.", findings=[finding])
    model = ScriptedModel(script=[answer])

    record = await run.one_prompt_audit(OnePrompt(model), "exam")(offer, [], 0)
    assert record.system == "one_prompt"
    assert [problem.rule for problem in record.report.problems] == [Rule.MANUFACTURER_INFO]
    assert record.cited == {Rule.MANUFACTURER_INFO: [("gpsr", "19")]}
    assert {result["rule"] for result in record.results} == {rule.value for rule in run.CODE_RULES}
    assert (record.trace_id, record.tokens, record.cost) == (None, {}, 0.0)
    assert len(model.requests) == 1


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
def database(owner, monkeypatch, tmp_path):
    """The database, with the photo check left to a person and results kept in tmp_path."""
    monkeypatch.setattr("listingaudit.photos.check.get_clip", UnsureClip)
    monkeypatch.setattr(settings, "vision_api_key", None)
    monkeypatch.setattr(records, "RESULTS_DIR", tmp_path)
    return owner


def writes_a_report(messages) -> ReportDraft:
    """The model's draft, quoting the first passage the searches found."""
    text = messages[-1].content
    heading = "The rulebook passages found for them:\n"
    passages, _ = json.JSONDecoder().raw_decode(text, text.index(heading) + len(heading))
    finding = FindingDraft(
        rule=Rule.PRIOR_PRICE,
        explanation="The badge says -42%, but the price is only 25.0% below €19.99.",
        suggested_fix="Show -25% on the badge, or leave the badge out.",
        quotes=[passages[0]["chunk_id"]],
    )
    return ReportDraft(summary="One possible problem: the -42% badge.", findings=[finding])


@pytest.mark.anyio
async def test_the_agent_records_what_it_chose_quoted_and_found(database):
    search = {"name": "search_rulebook", "args": {"query": "Article 6a prior price", "k": 3}}
    small = ScriptedModel(
        script=[
            KindChoice(kind="textile", reason="Scripted."),
            AIMessage("", tool_calls=[search | {"id": "call_0"}]),
            AIMessage("Each problem has a passage."),
            writes_a_report,
        ]
    )
    [(offer, history)] = load_listings(database, offer_ids=["P0171"])
    async with create_connected_server_and_client_session(server.mcp) as session:
        graph = build_graph(await Toolbox.connect(session), Brain(small))
        record = await run.agent_audit(graph, "practice")(offer, history, 2)

    assert (record.system, record.offer_id, record.repeat, record.kind) == (
        "agent",
        "P0171",
        2,
        "textile",
    )
    assert record.rules[0] == Rule.PRIOR_PRICE
    assert [problem.rule for problem in record.report.problems] == [Rule.PRIOR_PRICE]
    [(law, article)] = record.cited[Rule.PRIOR_PRICE]
    assert law in {"pid", "pid_guidance", "pangv", "aldi_sued"}
    assert article
    assert (record.trace_id, record.tokens, record.cost) == (None, {}, 0.0)


@pytest.mark.anyio
async def test_a_run_saves_the_split_and_runs_failed_audits_again(database, tmp_path):
    path = tmp_path / "exam" / "rules_only.jsonl.gz"
    await run.main_async("rules_only", "exam", limit=3, repeats=1, sample=None, concurrency=1)
    first = read_records(path)
    assert len(first) == 3
    assert (tmp_path / "exam" / "answer_key.json").exists()
    assert (tmp_path / "exam" / "listings.jsonl.gz").exists()

    failed = first[0].model_copy(update={"report": None, "error": "RuntimeError: Scripted."})
    records.write_records(path, [failed, *first[1:]])
    await run.main_async("rules_only", "exam", limit=3, repeats=1, sample=None, concurrency=1)
    again = read_records(path)
    assert sorted(record.key for record in again) == sorted(record.key for record in first)
    assert all(record.error is None for record in again)
