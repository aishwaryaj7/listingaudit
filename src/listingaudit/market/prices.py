"""Prices and 90-day price histories.

Money is always a whole number of cents, never a float. Floats can't store most
decimal amounts exactly (0.1 + 0.2 is 0.30000000000000004), and a price checker
has to be right to the cent.

Each function below tells one "price story": how the price moved over the last
90 days and what the listing shows today. Some stories follow the 30-day rule,
some break it on purpose.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

HISTORY_DAYS = 90
RULE_DAYS = 30  # the 30-day rule: compare with the lowest price of the 30 days before

# Typical prices in euros: (median, spread). The spread is the sigma of a log-normal
# distribution: with 0.5, most prices fall between about 0.4x and 2.7x the median.
TYPICAL_PRICE = {
    "RUG": (79, 0.6),
    "PILLOW": (24, 0.4),
    "FLAT_SHEET": (29, 0.4),
    "HOME_BED_AND_BATH": (34, 0.5),
    "CURTAIN": (32, 0.4),
    "LAMP": (49, 0.5),
    "LIGHT_FIXTURE": (69, 0.5),
    "HOME_LIGHTING_AND_LAMPS": (45, 0.5),
    "LIGHT_BULB": (9, 0.5),
    "CANDLE": (14, 0.5),
    "CHAIR": (129, 0.5),
    "SOFA": (549, 0.4),
    "TABLE": (159, 0.5),
    "HOME_FURNITURE_AND_DECOR": (39, 0.6),
    "SHOES": (59, 0.4),
}
# Goods sold by weight or volume are priced per kg or litre, then scaled to the pack
TYPICAL_PRICE_PER_KG_OR_L = {
    "GROCERY": (11, 0.5),
    "COFFEE": (24, 0.3),
    "TEA": (45, 0.4),
    "SNACK_FOOD_BAR": (22, 0.3),
    "SKIN_CLEANING_AGENT": (11, 0.4),
    "SKIN_MOISTURIZER": (24, 0.5),
    "SHAMPOO": (12, 0.4),
    "CLEANING_AGENT": (5, 0.4),
}


def charm(euros: float) -> int:
    """Round to a shop-style price in cents: 41.37 -> 4099, 3.37 -> 349.

    Prices from 10 euros end in .99; cheaper ones end in .49 or .99.
    """
    if euros >= 10:
        return round(euros) * 100 - 1
    return max(99, round(euros * 2) * 50 - 1)


def euros(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def percent_off(was: int, now: int) -> int:
    """The discount in whole percent, rounded down: never more than it really is."""
    return (was - now) * 100 // was


def base_price(product_type: str, rng: np.random.Generator, pack_total: float | None = None) -> int:
    """A believable price for a product, in cents."""
    if pack_total is not None:
        median, spread = TYPICAL_PRICE_PER_KG_OR_L[product_type]
        return max(199, charm(median * rng.lognormal(0, spread) * pack_total / 1000))
    median, spread = TYPICAL_PRICE.get(product_type, (30, 0.5))
    return charm(median * rng.lognormal(0, spread))


def lower_price(price: int, rate: float) -> int:
    """`price` reduced by about `rate` (0.2 = 20%), as a shop price that is really lower."""
    lowered = charm(price / 100 * (1 - rate))
    while lowered >= price:
        lowered -= 50
    return max(lowered, 49)


def higher_price(price: int, rate: float) -> int:
    raised = charm(price / 100 * (1 + rate))
    return raised if raised > price else price + 100


def history_dates(snapshot: date) -> list[date]:
    """The 90 days of a price history, oldest first; the last day is the snapshot day."""
    return [snapshot - timedelta(days=HISTORY_DAYS - 1 - i) for i in range(HISTORY_DAYS)]


def lowest_before(history: list[int], start: int, days: int = RULE_DAYS) -> int:
    """Lowest price in the `days` days before index `start` (the 30-day rule)."""
    return min(history[start - days : start])


@dataclass
class PriceStory:
    """How a price moved over 90 days, and what the listing shows today."""

    history: list[int]  # price per day in cents, oldest first; the last one is today
    was_price: int | None = None  # the crossed-out price next to today's price
    was_price_kind: str | None = None  # "previous_price" or "rrp"
    discount: int | None = None  # the "-20%" badge
    sale_start: int | None = None  # index in `history` where the reduction started
    facts: dict | None = None  # what the answer key needs to know about this story

    @property
    def price(self) -> int:
        return self.history[-1]


def _flat(price: int) -> list[int]:
    return [price] * HISTORY_DAYS


def _sale_start(days_ago: int) -> int:
    return HISTORY_DAYS - 1 - days_ago


def steady(base: int, rng: np.random.Generator) -> PriceStory:
    """No reduction announced. Sometimes the price changed once, weeks ago."""
    history = _flat(base)
    if rng.random() < 0.5:
        changed = int(rng.integers(10, 55))
        rate = float(rng.choice([0.1, 0.15]))
        old = higher_price(base, rate) if rng.random() < 0.5 else lower_price(base, rate)
        history[:changed] = [old] * changed
    return PriceStory(history)


def honest_sale(base: int, rng: np.random.Generator, days_ago: int | None = None) -> PriceStory:
    """A correct sale: the 'was' price is the lowest price of the 30 days before it."""
    days_ago = int(rng.integers(0, 9)) if days_ago is None else days_ago
    start = _sale_start(days_ago)
    sale = lower_price(base, float(rng.choice([0.1, 0.15, 0.2, 0.25, 0.3])))
    history = _flat(base)
    history[start:] = [sale] * (HISTORY_DAYS - start)
    if rng.random() < 0.3:  # an older, higher price long before the sale
        older = int(rng.integers(5, start - RULE_DAYS - 5))
        history[:older] = [higher_price(base, 0.2)] * older
    return PriceStory(history, base, "previous_price", percent_off(base, sale), start)


def sale_started_earlier(base: int, rng: np.random.Generator) -> PriceStory:
    """Trap: a correct sale that started 12 to 25 days ago.

    The lowest price of the *last* 30 days is the sale price itself, so a checker
    that looks at the wrong 30 days thinks the 'was' price is wrong.
    """
    days_ago = int(rng.integers(12, 26))
    story = honest_sale(base, rng, days_ago)
    story.facts = {"sale_started_days_ago": days_ago}
    return story


def long_price_rise(base: int, rng: np.random.Generator) -> PriceStory:
    """Trap: the price went up 31 to 45 days before the sale, then came down again.

    It looks like a pumped price, but the higher price lasted longer than 30 days,
    so it really is the lowest price of the 30 days before the sale.
    """
    start = _sale_start(int(rng.integers(0, 7)))
    high_days = int(rng.integers(31, 46))
    high = higher_price(base, float(rng.uniform(0.2, 0.5)))
    sale = base if rng.random() < 0.5 else lower_price(high, 0.25)
    history = _flat(base)
    history[start - high_days : start] = [high] * high_days
    history[start:] = [sale] * (HISTORY_DAYS - start)
    story = PriceStory(history, high, "previous_price", percent_off(high, sale), start)
    story.facts = {"days_at_higher_price": high_days}
    return story


def rrp_comparison(base: int, rng: np.random.Generator) -> PriceStory:
    """Trap: shows the manufacturer's recommended price (RRP), not an earlier price.

    That is a price comparison, not a price reduction, so the 30-day rule doesn't
    apply, even though the seller never charged the RRP.
    """
    story = PriceStory(_flat(base))
    story.was_price = higher_price(base, float(rng.uniform(0.2, 0.6)))
    story.was_price_kind = "rrp"
    return story


def unannounced_drop(base: int, rng: np.random.Generator) -> PriceStory:
    """Trap: the price went down a few days ago, but no reduction is announced."""
    days_ago = int(rng.integers(2, 11))
    start = _sale_start(days_ago)
    history = _flat(base)
    history[start:] = [lower_price(base, 0.15)] * (HISTORY_DAYS - start)
    story = PriceStory(history)
    story.facts = {"price_dropped_days_ago": days_ago}
    return story


def pumped_price(base: int, rng: np.random.Generator, hard: bool) -> PriceStory:
    """Problem: the price is raised for a few days, then 'reduced' back.

    The 'was' price shown is the short-lived high price, not the lowest price of
    the 30 days before the sale. Easy: a week or more at the high price and no real
    discount at all. Hard: only 3 or 4 days, and a small real discount exists.
    """
    start = _sale_start(int(rng.integers(0, 7)))
    if hard:
        high_days = int(rng.integers(3, 5))
        high = higher_price(base, float(rng.uniform(0.25, 0.45)))
        sale = lower_price(base, float(rng.uniform(0.05, 0.1)))
    else:
        high_days = int(rng.integers(7, 15))
        high = higher_price(base, float(rng.uniform(0.5, 1.0)))
        sale = base
    history = _flat(base)
    history[start - high_days : start] = [high] * high_days
    history[start:] = [sale] * (HISTORY_DAYS - start)
    story = PriceStory(history, high, "previous_price", percent_off(high, sale), start)
    story.facts = {
        "days_at_high_price": high_days,
        "lowest_price_30_days_before_sale": euros(base),
        "real_discount_percent": max(0, percent_off(base, sale)),
    }
    return story


def sneaky_percentage(base: int, rng: np.random.Generator, hard: bool) -> PriceStory:
    """Problem: the 'was' price is right, but the % badge uses an older, higher price.

    The EU Court of Justice (Aldi Süd, 2024) said the percentage must also be
    based on the lowest price of the 30 days before the sale. Easy: the badge is
    15 to 30 points too high. Hard: only 5 to 8 points too high.
    """
    start = _sale_start(int(rng.integers(0, 7)))
    old_until = start - int(rng.integers(35, 50))
    sale = lower_price(base, float(rng.choice([0.1, 0.15, 0.2, 0.25])))
    real = percent_off(base, sale)
    low, high = (5, 8) if hard else (15, 30)
    target = real + int(rng.integers(low, high + 1))
    old = higher_price(sale, target / (100 - target))
    while percent_off(old, sale) < real + low:
        old += 100
    history = _flat(base)
    history[:old_until] = [old] * old_until
    history[start:] = [sale] * (HISTORY_DAYS - start)
    story = PriceStory(history, base, "previous_price", percent_off(old, sale), start)
    story.facts = {"real_discount_percent": real, "badge_calculated_from": euros(old)}
    return story
