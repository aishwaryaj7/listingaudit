"""Money, unit prices, and the 90-day price stories."""

from decimal import Decimal

import numpy as np
import pytest

from listingaudit.market import prices
from listingaudit.market.listing import unit_price
from listingaudit.market.models import money
from listingaudit.market.prices import (
    HISTORY_DAYS,
    RULE_DAYS,
    charm,
    euros,
    higher_price,
    lower_price,
    lowest_before,
    percent_off,
)

SEEDS = range(200)
BASE_PRICES = [99, 349, 1999, 54999]  # in cents: from 0.99 to 549.99 euros


def tell(story, **options):
    """The same price story, told with many seeds and base prices."""
    for seed in SEEDS:
        yield story(BASE_PRICES[seed % len(BASE_PRICES)], np.random.default_rng(seed), **options)


def sale_start(history: list[int]) -> int:
    """The day the current price began, read from the history alone (as the agent must)."""
    start = len(history) - 1
    while start > 0 and history[start - 1] == history[-1]:
        start -= 1
    return start


# --- Money ---------------------------------------------------------------------------


@pytest.mark.parametrize(("amount", "cents"), [(41.37, 4099), (3.37, 349), (3.0, 299), (0.2, 99)])
def test_charm_prices_look_like_shop_prices(amount, cents):
    assert charm(amount) == cents


def test_money_is_exact_and_has_two_decimals():
    assert money(3999) == Decimal("39.99")
    assert str(money(70)) == "0.70"
    assert money(None) is None
    assert euros(5) == "0.05"


@pytest.mark.parametrize(
    ("was", "now", "percent"), [(1000, 800, 20), (1000, 799, 20), (999, 800, 19)]
)
def test_percent_off_rounds_down(was, now, percent):
    # 19.9% shows as 19%: a badge never promises more than the real discount
    assert percent_off(was, now) == percent


def test_lower_and_higher_prices_really_change():
    for price in [99, 149, 999, 1999, 54999]:
        for rate in [0.05, 0.1, 0.25, 0.5]:
            assert lower_price(price, rate) < price < higher_price(price, rate)


def test_unit_price_is_per_kg_or_litre():
    assert unit_price(199, 283, "g") == (Decimal("7.03"), "kg")  # 1.99 for 283 g
    assert unit_price(299, 500, "ml") == (Decimal("5.98"), "l")
    assert unit_price(199, 283, "g", per=100) == (Decimal("0.70"), "100 g")


def test_unit_price_rounds_half_a_cent_up():
    # 0.01 for 400 g is 0.025 per kg. Python's round() would give 0.02 ("banker's
    # rounding"); shops round half a cent up.
    assert unit_price(1, 400, "g") == (Decimal("0.03"), "kg")


# --- Price stories -----------------------------------------------------------------------

EVERY_STORY = [
    (prices.steady, {}),
    (prices.honest_sale, {}),
    (prices.sale_started_earlier, {}),
    (prices.long_price_rise, {}),
    (prices.rrp_comparison, {}),
    (prices.unannounced_drop, {}),
    (prices.pumped_price, {"hard": False}),
    (prices.pumped_price, {"hard": True}),
    (prices.sneaky_percentage, {"hard": False}),
    (prices.sneaky_percentage, {"hard": True}),
]


@pytest.mark.parametrize(("story", "options"), EVERY_STORY)
def test_every_story_covers_90_days(story, options):
    for told in tell(story, **options):
        assert len(told.history) == HISTORY_DAYS
        assert min(told.history) > 0
        if told.was_price is not None:
            assert told.was_price > told.price


@pytest.mark.parametrize(
    "story", [prices.honest_sale, prices.sale_started_earlier, prices.long_price_rise]
)
def test_legal_reductions_follow_the_30_day_rule(story):
    for told in tell(story):
        start = sale_start(told.history)
        assert start == told.sale_start
        assert told.was_price == lowest_before(told.history, start)
        assert told.discount == percent_off(told.was_price, told.price)


def test_a_sale_that_started_earlier_only_looks_wrong():
    for told in tell(prices.sale_started_earlier):
        # The lowest price of the *last* 30 days is the sale price itself...
        assert min(told.history[-RULE_DAYS:]) == told.price
        # ...but the rule looks at the 30 days before the sale started
        assert told.was_price == lowest_before(told.history, told.sale_start)
        assert 12 <= told.facts["sale_started_days_ago"] <= 25


def test_a_long_price_rise_lasted_more_than_30_days():
    for told in tell(prices.long_price_rise):
        assert told.facts["days_at_higher_price"] > RULE_DAYS
        window = told.history[told.sale_start - RULE_DAYS : told.sale_start]
        assert set(window) == {told.was_price}


@pytest.mark.parametrize("hard", [False, True])
def test_pumped_prices_break_the_30_day_rule(hard):
    for told in tell(prices.pumped_price, hard=hard):
        start = sale_start(told.history)
        lowest = lowest_before(told.history, start)
        assert told.was_price > lowest
        assert told.facts["lowest_price_30_days_before_sale"] == euros(lowest)
        real = told.facts["real_discount_percent"]
        assert real == max(0, percent_off(lowest, told.price))
        if hard:  # only a few days at the high price, and a small real discount
            assert told.facts["days_at_high_price"] in (3, 4)
            assert real > 0
        else:
            assert told.facts["days_at_high_price"] >= 7
            assert real == 0


@pytest.mark.parametrize(("hard", "too_high_by"), [(False, 15), (True, 5)])
def test_sneaky_badges_come_from_an_older_price(hard, too_high_by):
    for told in tell(prices.sneaky_percentage, hard=hard):
        start = sale_start(told.history)
        # The crossed-out price is right...
        assert told.was_price == lowest_before(told.history, start)
        # ...but the badge is worked out from a price older than 30 days
        real = percent_off(told.was_price, told.price)
        assert told.facts["real_discount_percent"] == real
        assert told.discount >= real + too_high_by
        oldest = max(told.history)
        assert told.facts["badge_calculated_from"] == euros(oldest)
        assert told.discount == percent_off(oldest, told.price)
        assert oldest not in told.history[start - RULE_DAYS : start]


def test_an_rrp_is_a_comparison_not_a_reduction():
    for told in tell(prices.rrp_comparison):
        assert told.was_price_kind == "rrp"
        assert told.discount is None
        assert told.was_price not in told.history  # the seller never charged it


@pytest.mark.parametrize("story", [prices.steady, prices.unannounced_drop])
def test_some_stories_announce_no_reduction(story):
    for told in tell(story):
        assert told.was_price is None
        assert told.discount is None


def test_an_unannounced_drop_is_a_real_drop():
    for told in tell(prices.unannounced_drop):
        assert told.price < told.history[0]
        days_ago = told.facts["price_dropped_days_ago"]
        assert sale_start(told.history) == HISTORY_DAYS - 1 - days_ago
