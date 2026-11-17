"""Suggested fixes, applied to their listings and checked again. The model is scripted."""

import gzip
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from listingaudit.agent.report import CHECKLIST, Problem, Report
from listingaudit.agent.scripted import ScriptedModel
from listingaudit.eval import fixes, records
from listingaudit.eval.fixes import SELLER, Change, Changes, apply, as_number, try_fix
from listingaudit.eval.records import AnswerKey, Record, read_json_lines, write_records
from listingaudit.market.models import PricePoint
from listingaudit.market.models import Problem as Planted
from listingaudit.market.rules import Rule

TODAY = date(2026, 11, 2)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def history(offer_id: str, *runs: tuple[int, str]) -> list[PricePoint]:
    prices = [Decimal(price) for days, price in runs for _ in range(days)]
    first = TODAY - timedelta(days=len(prices) - 1)
    return [
        PricePoint(offer_id=offer_id, day=first + timedelta(days=i), price=price)
        for i, price in enumerate(prices)
    ]


def possible_problem(rule: Rule, fix: str) -> Problem:
    spec = CHECKLIST.rules[rule]
    return Problem(
        rule=rule,
        name=spec.name,
        severity=spec.severity,
        explanation="Scripted.",
        evidence=[],
        facts={},
        law=[],
        quotes=[],
        suggested_fix=fix,
    )


@pytest.fixture
def badge(make_offer):
    """A -30% badge on a price only 20% below the lowest price of the 30 days before."""
    offer = make_offer(
        offer_id="E0001",
        price="40.00",
        was_price="50.00",
        was_price_kind="previous_price",
        discount_percent=30,
    )
    return offer, history("E0001", (60, "50.00"), (5, "40.00"))


@pytest.fixture
def no_address(make_offer):
    return make_offer(offer_id="E0002", manufacturer_address=None), []


def test_a_badge_lowered_to_the_number_the_fix_states_passes(badge):
    problem = possible_problem(Rule.PRIOR_PRICE, "Show -20% on the badge, the real discount.")
    outcome, summary = try_fix(badge, problem, [Change(field="discount_percent", value="-20%")])
    assert outcome == "pass"
    assert summary.endswith("The badge -20% fits a 20.0% reduction.")


def test_a_number_the_fix_doesnt_state_makes_it_vague(badge):
    problem = possible_problem(Rule.PRIOR_PRICE, "Lower the badge to the real discount.")
    outcome, detail = try_fix(badge, problem, [Change(field="discount_percent", value="20")])
    assert (outcome, detail) == ("vague", "The fix doesn't state discount_percent = 20.")


def test_leaving_out_the_crossed_out_price_takes_its_badge_with_it(badge):
    problem = possible_problem(Rule.PRIOR_PRICE, "Leave out the crossed-out price and the badge.")
    fixed, outcome, _ = apply(badge[0], [Change(field="was_price", value=None)], "")
    assert outcome == "pass"
    assert (fixed.was_price, fixed.was_price_kind, fixed.discount_percent) == (None, None, None)
    assert try_fix(badge, problem, [Change(field="was_price", value=None)])[0] == "pass"


def test_a_change_the_listing_cannot_take_is_invalid(badge):
    problem = possible_problem(Rule.PRIOR_PRICE, "Show €35.00 as the earlier price.")
    outcome, detail = try_fix(badge, problem, [Change(field="was_price", value="35.00")])
    assert outcome == "invalid"
    assert "higher than the price" in detail


def test_a_fix_that_changes_nothing_fails_the_check_again(badge):
    problem = possible_problem(Rule.PRIOR_PRICE, "Check the badge.")
    outcome, summary = try_fix(badge, problem, [])
    assert outcome == "fail"
    assert "The badge says -30%" in summary


def test_information_only_the_seller_has_gets_a_sample_value(no_address):
    problem = possible_problem(Rule.MANUFACTURER_INFO, "Add the manufacturer's postal address.")
    change = Change(field="manufacturer_address", value=SELLER)
    fixed, _, _ = apply(no_address[0], [change], problem.suggested_fix)
    assert fixed.manufacturer_address == "1 Example Street, Example City, Germany"
    assert try_fix(no_address, problem, [change])[0] == "pass"


def test_a_number_left_to_the_seller_makes_the_fix_vague(no_address):
    outcome, detail = apply(no_address[0], [Change(field="price", value=SELLER)], "")[1:]
    assert (outcome, detail) == ("vague", "The fix leaves price to the seller.")


@pytest.mark.parametrize(
    ("written", "number"),
    [("€1,310.99", "1310.99"), ("40,00 €", "40.00"), ("-20%", "20"), ("39.99", "39.99")],
)
def test_numbers_are_read_the_way_a_fix_writes_them(written, number):
    assert as_number(written) == Decimal(number)


@pytest.mark.anyio
async def test_the_model_gets_the_listing_the_problem_and_the_fix(badge):
    answer = Changes(changes=[Change(field="discount_percent", value="20")])
    model = ScriptedModel(script=[answer])
    problem = possible_problem(Rule.PRIOR_PRICE, "Show -20% on the badge.")

    assert await fixes.changes_for(model, badge[0], problem) == answer.changes
    [system, request] = model.requests[0]
    assert "Never work out a number yourself" in system.content
    assert '"discount_percent": 30' in request.content
    assert '"title"' not in request.content  # only the fields a fix may change
    assert request.content.endswith("The suggested fix:\nShow -20% on the badge.")


@pytest.mark.anyio
async def test_only_real_problems_from_code_checks_are_tried_and_a_run_carries_on(
    badge, no_address, tmp_path, monkeypatch
):
    monkeypatch.setattr(records, "RESULTS_DIR", tmp_path)
    listings = {"E0001": badge, "E0002": no_address}
    folder = tmp_path / "exam"
    folder.mkdir()
    with gzip.open(folder / "listings.jsonl.gz", "wt", encoding="utf-8") as out:
        for offer, prices in listings.values():
            row = {
                "listing": offer.model_dump(mode="json"),
                "history": [(str(p.day), str(p.price)) for p in prices],
            }
            out.write(json.dumps(row) + "\n")
    planted = [
        Planted(
            offer_id="E0001",
            problem="sneaky_percentage",
            rule=Rule.PRIOR_PRICE,
            difficulty="easy",
            evidence={},
        ),
        Planted(
            offer_id="E0002",
            problem="missing_manufacturer_info",
            rule=Rule.MANUFACTURER_INFO,
            difficulty="easy",
            evidence={},
        ),
    ]
    key = AnswerKey(split="exam", cases=[], problems=planted, traps=[])
    (folder / "answer_key.json").write_text(key.model_dump_json())

    def record(offer_id: str, *problems: Problem, repeat: int = 0) -> Record:
        report = Report(
            offer_id=offer_id,
            title="Test Pillow",
            kind="textile",
            summary="Scripted.",
            problems=list(problems),
            to_check=[],
            passed=[],
            unresolved=[],
        )
        return Record(system="agent", offer_id=offer_id, repeat=repeat, report=report)

    lower = possible_problem(Rule.PRIOR_PRICE, "Show -20% on the badge.")
    address = possible_problem(Rule.MANUFACTURER_INFO, "Add the manufacturer's postal address.")
    false_alarm = possible_problem(Rule.FIBRE_COMPOSITION, "Add the fibres.")
    photo = possible_problem(Rule.PHOTO_MATCHES_TEXT, "Use a photo of the product.")
    write_records(
        folder / "agent.jsonl.gz",
        [
            record("E0001", lower, false_alarm),
            record("E0002", address, photo),
            record("E0002", address, repeat=1),
        ],
    )

    def busy(messages):
        raise RuntimeError("The model is busy.")

    model = ScriptedModel(
        script=[Changes(changes=[Change(field="discount_percent", value="20")]), busy]
    )
    monkeypatch.setattr(fixes, "chat_model", lambda name: model)

    await fixes.main_async("exam", concurrency=1)
    rows = read_json_lines(folder / "fixes.jsonl")
    assert [(row["offer_id"], row["rule"], row["outcome"]) for row in rows] == [
        ("E0001", "prior_price", "pass"),
        ("E0002", "manufacturer_info", "error"),
    ]

    model.script = [Changes(changes=[Change(field="manufacturer_address", value=SELLER)])]
    await fixes.main_async("exam", concurrency=1)
    rows = read_json_lines(folder / "fixes.jsonl")
    assert [(row["offer_id"], row["outcome"]) for row in rows] == [
        ("E0001", "pass"),
        ("E0002", "pass"),
    ]
    assert rows[1]["changes"] == [{"field": "manufacturer_address", "value": SELLER}]
