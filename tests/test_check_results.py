"""The result every check returns."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from listingaudit.checks.results import CheckResult, Finding, citations, euro, is_missing, ok
from listingaudit.market.rules import Rule


def test_a_problem_needs_a_finding():
    with pytest.raises(ValidationError, match="a problem needs findings"):
        CheckResult(rule=Rule.UNIT_PRICE, status="problem", summary="Something is wrong.")


def test_only_a_problem_has_findings():
    finding = Finding(code="unit_price_missing", message="No price per kg is shown.")
    with pytest.raises(ValidationError):
        CheckResult(rule=Rule.UNIT_PRICE, status="ok", summary="Fine.", findings=[finding])


def test_every_rule_cites_the_law():
    for rule in Rule:
        assert citations(rule)


def test_citations_name_the_article_and_paragraph():
    assert "GPSR Art. 19(a)" in citations(Rule.MANUFACTURER_INFO)
    assert "PAngV § 11(1)" in citations(Rule.PRIOR_PRICE)
    assert "Case C-330/23 (Aldi Süd), operative part" in citations(Rule.PRIOR_PRICE)


def test_results_carry_their_rules_citations():
    result = ok(Rule.ENERGY_LABEL, "Fine.")
    assert result.law == list(citations(Rule.ENERGY_LABEL))


def test_results_turn_into_json():
    result = ok(Rule.PRIOR_PRICE, "Fine.", price=Decimal("39.99"), sale_started=date(2026, 11, 2))
    assert result.model_dump(mode="json")["facts"] == {
        "price": "39.99",
        "sale_started": "2026-11-02",
    }


@pytest.mark.parametrize(
    "value, missing",
    [(None, True), ("", True), ("   ", True), ("N/A", True), ("-", True), ("Berlin", False)],
)
def test_blank_values_count_as_missing(value, missing):
    assert is_missing(value) is missing


def test_euro_rounds_half_up_to_the_cent():
    assert euro(Decimal("2.905")) == "€2.91"
    assert euro(Decimal("3")) == "€3.00"
