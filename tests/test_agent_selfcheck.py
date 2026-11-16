"""The self-check: plain code that holds the model's draft to the tool results."""

from decimal import Decimal

import pytest

from listingaudit.agent.report import FindingDraft, ReportDraft
from listingaudit.agent.selfcheck import amounts, check_draft, counts
from listingaudit.checks.results import Finding, ok, problem
from listingaudit.market.rules import Rule

BADGE = "The badge says -42%, but the price is only 25.0% below €19.99, the lowest price."
PRICE = problem(
    Rule.PRIOR_PRICE,
    [Finding(code="badge_too_high", message=BADGE, facts={"badge_percent": 42})],
    price="14.99",
    was_price="19.99",
    discount_percent=42,
    lowest_price="19.99",
    real_discount_percent="25.0",
)
MAKER = ok(Rule.MANUFACTURER_INFO, "The manufacturer is named, with a postal address.")
RESULTS = [PRICE, MAKER]
PASSAGES = {"pid/6a#1": {"chunk_id": "pid/6a#1", "citation": "Directive 98/6/EC Art. 6a(2)"}}


def finding(explanation: str = "The -42% badge should be 25.0%.", **changes) -> FindingDraft:
    fields = {
        "rule": Rule.PRIOR_PRICE,
        "explanation": explanation,
        "suggested_fix": "Show -25% or remove the badge.",
        "quotes": ["pid/6a#1"],
    }
    return FindingDraft(**(fields | changes))


def draft(
    *findings: FindingDraft, summary: str = "One possible problem: the badge."
) -> ReportDraft:
    return ReportDraft(summary=summary, findings=list(findings))


def test_a_draft_that_keeps_to_the_check_results_passes():
    assert check_draft(draft(finding()), RESULTS, PASSAGES) == []


def test_each_problem_needs_exactly_one_finding():
    assert check_draft(draft(), RESULTS, PASSAGES) == [
        f"The prior_price check found a problem, but the draft has no finding for it: {BADGE}"
    ]
    assert check_draft(draft(finding(), finding()), RESULTS, PASSAGES) == [
        "The draft has 2 findings for prior_price; write one."
    ]


def test_a_finding_for_a_rule_that_passed_is_refused():
    extra = finding("The address looks incomplete.", rule=Rule.MANUFACTURER_INFO)
    assert check_draft(draft(finding(), extra), RESULTS, PASSAGES) == [
        "The draft has a finding for manufacturer_info, but its check found no problem. "
        "Leave it out."
    ]


def test_quotes_come_only_from_the_search_results():
    feedback = check_draft(draft(finding(quotes=["pid/6a#1", "pid/6a#9"])), RESULTS, PASSAGES)
    assert feedback == [
        "The finding for prior_price quotes 'pid/6a#9', which the rulebook search didn't "
        "return. Quote only passages from the search results."
    ]


def test_a_finding_needs_an_explanation_and_a_fix():
    feedback = check_draft(draft(finding(" ", suggested_fix="")), RESULTS, PASSAGES)
    assert feedback == ["The finding for prior_price needs an explanation and a fix."]


@pytest.mark.parametrize(
    "explanation",
    [
        "The badge says -42 %, but the real discount from €19.99 to €14.99 is 25%.",
        "Only 25 per cent off 19,99 €, not 42%.",
    ],
)
def test_the_checks_own_numbers_pass_rounded_or_not(explanation):
    assert check_draft(draft(finding(explanation)), RESULTS, PASSAGES) == []


def test_numbers_the_model_works_out_itself_are_refused():
    explanation = "The price is €5.00 lower, a 25.01% cut, not 42%."
    assert check_draft(draft(finding(explanation)), RESULTS, PASSAGES) == [
        "The finding for prior_price states 5.00, a number its check didn't give. "
        "Use only the check's own prices and percentages.",
        "The finding for prior_price states 25.01, a number its check didn't give. "
        "Use only the check's own prices and percentages.",
    ]
    feedback = check_draft(draft(finding(), summary="A badge 17% too high."), RESULTS, PASSAGES)
    assert feedback == ["The summary states 17, a number no check gave."]


def test_the_summary_gives_the_number_of_problems_the_checks_found():
    wrong = draft(finding(), summary="There are 2 possible problems; the worst is the badge.")
    assert check_draft(wrong, RESULTS, PASSAGES) == [
        "The summary gives 2 as the number of possible problems; the checks found 1."
    ]
    assert counts("One possible problem, not three issues; 2 problems; no problems") == [1, 3, 2]


def test_a_draft_never_calls_the_listing_illegal():
    explanation = "This violates Article 6a: the badge is illegal, and illegal badges mislead."
    feedback = check_draft(draft(finding(explanation)), RESULTS, PASSAGES)
    assert [line.split('"')[1] for line in feedback] == ["violates", "illegal"]
    assert all("possible problem" in line for line in feedback)


def test_amounts_reads_prices_and_percentages_only():
    text = "Now €14.99, was 19,99 €, a 42% badge, 25.0 % off, €1,310.99, 30 days, 2026-10-28"
    assert amounts(text) == {Decimal(n) for n in ("14.99", "19.99", "42", "25.0", "1310.99")}
    assert amounts("Article 6a(2), section 2.1, 1 kg, 12.3 oz") == set()
