"""A person's review of a report: what it must hold, and the seller message it leads to."""

import pytest
from pydantic import ValidationError

from listingaudit.agent.graph import step_line
from listingaudit.agent.report import (
    Decision,
    Problem,
    Report,
    Review,
    review_errors,
    seller_message,
)
from listingaudit.market.rules import Rule

PRICE = "Honest discounts (the 30-day rule)"
MAKER = "Manufacturer named, with an address"


def problem(rule: Rule, name: str, fix: str = "Fix it.") -> Problem:
    return Problem(
        rule=rule,
        name=name,
        severity="high",
        explanation=f"What is wrong with the {rule}.",
        evidence=[],
        facts={},
        law=[f"Law of the {rule}"],
        quotes=[],
        suggested_fix=fix,
    )


REPORT = Report(
    offer_id="P0042",
    title="Test Pillow",
    kind="textile",
    summary="2 possible problems.",
    problems=[
        problem(Rule.PRIOR_PRICE, PRICE, fix="Show -25% on the badge."),
        problem(Rule.MANUFACTURER_INFO, MAKER, fix=""),
    ],
    to_check=[],
    passed=[],
    unresolved=[],
)


def review(*decisions: Decision) -> Review:
    return Review(reviewer="Test Reviewer", decisions=list(decisions))


def approve(rule: Rule, fix: str = "") -> Decision:
    return Decision(rule=rule, approved=True, fix=fix)


def reject(rule: Rule, reason: str = "The photo is just dark.") -> Decision:
    return Decision(rule=rule, approved=False, reason=reason)


def test_a_complete_review_has_no_errors():
    complete = review(approve(Rule.PRIOR_PRICE), reject(Rule.MANUFACTURER_INFO))
    assert review_errors(REPORT, complete) == []


def test_a_review_decides_on_each_finding_once():
    assert review_errors(REPORT, review(approve(Rule.PRIOR_PRICE))) == [
        f"Approve or reject the finding on {MAKER}."
    ]
    twice = review(
        approve(Rule.PRIOR_PRICE), reject(Rule.PRIOR_PRICE), reject(Rule.MANUFACTURER_INFO)
    )
    assert review_errors(REPORT, twice) == [f"Decide only once on {PRICE}."]
    stray = review(
        approve(Rule.PRIOR_PRICE), reject(Rule.MANUFACTURER_INFO), reject(Rule.UNIT_PRICE)
    )
    assert review_errors(REPORT, stray) == ["The report has no finding on unit_price."]


def test_a_rejection_needs_a_reason_and_an_approval_a_fix():
    no_reason = review(approve(Rule.PRIOR_PRICE), reject(Rule.MANUFACTURER_INFO, reason=" "))
    assert review_errors(REPORT, no_reason) == [f"Say why the finding on {MAKER} is rejected."]
    no_fix = review(approve(Rule.PRIOR_PRICE), approve(Rule.MANUFACTURER_INFO))
    assert review_errors(REPORT, no_fix) == [
        f"Write the fix for the seller on {MAKER}: the report has none."
    ]
    own_fix = review(approve(Rule.PRIOR_PRICE), approve(Rule.MANUFACTURER_INFO, fix="Add it."))
    assert review_errors(REPORT, own_fix) == []


def test_a_review_names_its_reviewer():
    with pytest.raises(ValidationError, match="reviewer"):
        Review(reviewer="", decisions=[])


def test_the_seller_message_holds_only_the_approved_findings():
    decided = review(approve(Rule.PRIOR_PRICE), reject(Rule.MANUFACTURER_INFO))
    message = seller_message(REPORT, decided, seller_id=7)

    assert message.seller_id == 7
    assert message.subject == "Your listing P0042: 1 possible problem to look at"
    assert message.body.splitlines()[2:8] == [
        'We have checked your listing P0042, "Test Pillow", and found 1 possible problem with '
        "the EU rules for selling to consumers. Please look at it and change the listing "
        "where needed.",
        "",
        f"1. {PRICE}",
        "What is wrong with the prior_price.",
        "What to change: Show -25% on the badge.",
        "Where the rule comes from: Law of the prior_price",
    ]
    assert "manufacturer" not in message.body.lower()
    assert "The photo is just dark." not in message.body


def test_the_reviewers_own_fix_replaces_the_suggested_one():
    decided = review(approve(Rule.PRIOR_PRICE), approve(Rule.MANUFACTURER_INFO, fix="Add it."))
    message = seller_message(REPORT, decided, seller_id=7)

    assert "found 2 possible problems" in message.body
    assert "Please look at them" in message.body
    assert "2. Manufacturer named, with an address" in message.body
    assert "What to change: Add it." in message.body


def test_no_message_when_nothing_is_approved():
    rejected = review(reject(Rule.PRIOR_PRICE), reject(Rule.MANUFACTURER_INFO))
    assert seller_message(REPORT, rejected, seller_id=7) is None


def test_the_review_steps_say_what_happened():
    decided = review(approve(Rule.PRIOR_PRICE), reject(Rule.MANUFACTURER_INFO))
    update = {"review": decided.model_dump(mode="json")}
    assert step_line("human_review", update) == "Test Reviewer approved 1 of 2"
    assert step_line("draft_message", {"message": None}) == "nothing to send"
    assert step_line("build_report", {"report": {}}) == "report ready"
