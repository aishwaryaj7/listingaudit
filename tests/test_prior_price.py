"""The 30-day rule: crossed-out prices and % badges against the price history."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from listingaudit.checks.prior_price import check_prior_price
from listingaudit.market.models import PricePoint

TODAY = date(2026, 11, 2)


def history(*runs: tuple[int, str]) -> list[PricePoint]:
    """A daily price history ending today, from (days, price) runs, oldest first."""
    prices = [Decimal(price) for days, price in runs for _ in range(days)]
    first = TODAY - timedelta(days=len(prices) - 1)
    return [
        PricePoint(offer_id="P0001", day=first + timedelta(days=i), price=price)
        for i, price in enumerate(prices)
    ]


@pytest.fixture
def sale(make_offer):
    """A listing that announces a reduction: price, crossed-out price, badge."""

    def make(price: str, was: str, badge: int | None = None, kind: str = "previous_price"):
        return make_offer(price=price, was_price=was, was_price_kind=kind, discount_percent=badge)

    return make


def codes(result) -> list[str]:
    return [finding.code for finding in result.findings]


def test_an_honest_sale_passes(sale):
    result = check_prior_price(sale("39.99", "49.99", 20), history((60, "49.99"), (5, "39.99")))
    assert result.status == "ok"
    assert result.facts["lowest_price"] == Decimal("49.99")
    assert result.facts["sale_started"] == TODAY - timedelta(days=4)


def test_a_pumped_price_is_caught(sale):
    # €44.99 for weeks, €79.99 for the last 5 days, then "50% off" to €39.99
    prices = history((60, "44.99"), (5, "79.99"), (1, "39.99"))
    result = check_prior_price(sale("39.99", "79.99", 50), prices)
    assert result.status == "problem"
    assert codes(result) == ["was_price_too_high", "badge_too_high"]
    assert result.facts["lowest_price"] == Decimal("44.99")
    assert result.facts["real_discount_percent"] == Decimal("11.1")
    assert result.findings[0].facts["days_at_was_price"] == 5


def test_a_was_price_never_charged_is_caught(sale):
    result = check_prior_price(sale("39.99", "99.99"), history((60, "44.99"), (1, "39.99")))
    assert codes(result) == ["was_price_too_high"]
    assert "wasn't charged on any of them" in result.summary


def test_a_badge_worked_out_from_an_older_price_is_caught(sale):
    # Right crossed-out price, but -33% comes from the €59.99 of two months ago
    prices = history((40, "59.99"), (49, "49.99"), (1, "39.99"))
    result = check_prior_price(sale("39.99", "49.99", 33), prices)
    assert codes(result) == ["badge_too_high"]
    assert result.facts["real_discount_percent"] == Decimal("20.0")


@pytest.mark.parametrize("badge, status", [(20, "ok"), (21, "problem")])
def test_a_badge_may_round_to_the_nearest_percent(sale, badge, status):
    # €50.00 -> €40.20 is 19.6% off: "-20%" is fine, "-21%" is not
    result = check_prior_price(sale("40.20", "50.00", badge), history((60, "50.00"), (3, "40.20")))
    assert result.status == status


def test_a_sale_that_started_weeks_ago_counts_back_from_its_first_day(sale):
    # The lowest price of the last 30 days is the sale price itself; that is not the
    # period the rule looks at
    result = check_prior_price(sale("39.99", "49.99", 20), history((60, "49.99"), (20, "39.99")))
    assert result.status == "ok"
    assert result.facts["sale_started"] == TODAY - timedelta(days=19)


def test_a_higher_price_that_lasted_more_than_30_days_is_a_fair_was_price(sale):
    prices = history((20, "49.99"), (40, "69.99"), (3, "49.99"))
    assert check_prior_price(sale("49.99", "69.99", 28), prices).status == "ok"


def test_a_comparison_with_the_recommended_price_is_not_a_reduction(sale):
    result = check_prior_price(sale("39.99", "59.99", kind="rrp"), history((90, "39.99")))
    assert result.status == "not_applicable"


def test_a_lower_price_without_a_was_price_announces_nothing(make_offer):
    prices = history((60, "49.99"), (5, "39.99"))
    assert check_prior_price(make_offer(price="39.99"), prices).status == "not_applicable"


def test_a_short_history_cannot_show_the_rule_was_kept(sale):
    result = check_prior_price(sale("39.99", "49.99"), history((10, "49.99"), (3, "39.99")))
    assert result.status == "cannot_check"
    assert result.facts["days_with_a_price"] == 10


def test_a_short_history_can_still_show_a_problem(sale):
    # Only 10 of the 30 days are known, but one of them already had a lower price
    prices = history((5, "39.99"), (5, "59.99"), (3, "44.99"))
    result = check_prior_price(sale("44.99", "59.99"), prices)
    assert codes(result) == ["was_price_too_high"]


def test_a_sale_older_than_the_history_cannot_be_checked(sale):
    result = check_prior_price(sale("39.99", "49.99"), history((90, "39.99")))
    assert result.status == "cannot_check"
    assert "since the price history starts" in result.summary


def test_a_sale_that_started_on_the_first_day_of_the_history_cannot_be_checked(sale):
    prices = history((5, "45.00"), (5, "40.00"))
    result = check_prior_price(sale("40.00", "50.00"), prices, sale_started=prices[0].day)
    assert result.status == "cannot_check"
    assert "no prices for the 30 days before" in result.summary


def test_a_reduction_in_steps_counts_back_from_the_first_step(sale):
    # €50 -> €45 -> €40, shown as "was €50" throughout (PAngV § 11(2))
    prices = history((60, "50.00"), (5, "45.00"), (5, "40.00"))
    first_step = TODAY - timedelta(days=9)
    assert check_prior_price(sale("40.00", "50.00"), prices).status == "problem"
    result = check_prior_price(sale("40.00", "50.00"), prices, sale_started=first_step)
    assert result.status == "ok"


def test_a_price_that_went_up_again_is_not_a_reduction_in_steps(sale):
    prices = history((60, "50.00"), (5, "40.00"), (5, "45.00"))
    started = TODAY - timedelta(days=9)
    result = check_prior_price(sale("45.00", "50.00"), prices, sale_started=started)
    assert result.status == "cannot_check"


def test_a_sale_start_outside_the_history_cannot_be_used(sale):
    prices = history((60, "50.00"), (5, "40.00"))
    result = check_prior_price(sale("40.00", "50.00"), prices, sale_started=TODAY + timedelta(1))
    assert result.status == "cannot_check"


def test_the_history_must_end_at_the_listings_price(sale):
    result = check_prior_price(sale("39.99", "49.99"), history((60, "49.99"), (5, "41.99")))
    assert result.status == "cannot_check"


def test_a_was_price_below_the_lowest_price_doesnt_overstate_the_discount(sale):
    result = check_prior_price(sale("40.00", "45.00"), history((60, "50.00"), (5, "40.00")))
    assert result.status == "ok"
    assert "below the lowest price" in result.summary
