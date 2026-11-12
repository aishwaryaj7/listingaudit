"""The 30-day rule for announced price reductions.

A listing that announces a reduction must show, as its earlier price, the lowest
price charged in the 30 days before the reduction started (Directive 98/6/EC
Art. 6a; PAngV § 11). A percentage badge has to be worked out from that price
too (Court of Justice, C-330/23 Aldi Süd).
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from listingaudit.checks.results import (
    CheckResult,
    Finding,
    cannot_check,
    euro,
    not_applicable,
    ok,
    problem,
)
from listingaudit.market.models import Offer, PricePoint
from listingaudit.market.rules import Rule

RULE = Rule.PRIOR_PRICE
DAYS = 30


def percent_below(reference: Decimal, price: Decimal) -> Decimal:
    """How far `price` is below `reference`, in percent (negative if above it)."""
    return (reference - price) * 100 / reference


def current_price_since(prices: dict[date, Decimal]) -> date:
    """The first day of the unbroken run of today's price."""
    today = max(prices)
    start = today
    while (day := start - timedelta(days=1)) in prices and prices[day] == prices[today]:
        start = day
    return start


def check_prior_price(
    offer: Offer, history: list[PricePoint], sale_started: date | None = None
) -> CheckResult:
    """Check the crossed-out price and the % badge against the 30-day rule.

    The reduction is taken to have started on the first day of the current price.
    For a reduction that grew step by step, pass `sale_started`, the day of the
    first step: the earlier price is then the lowest price of the 30 days before
    that day (PAngV § 11(2)).
    """
    if offer.was_price_kind is None:
        return not_applicable(RULE, "The listing announces no price reduction.")
    if offer.was_price_kind == "rrp":
        return not_applicable(
            RULE,
            f"The crossed-out price {euro(offer.was_price)} is the recommended retail price. "
            "A comparison with it is not a price reduction, so the 30-day rule doesn't apply.",
        )

    prices = {point.day: point.price for point in history}
    if not prices:
        return cannot_check(RULE, "The listing has no price history.")
    first, today = min(prices), max(prices)
    if prices[today] != offer.price:
        return cannot_check(
            RULE,
            f"The price history ends at {euro(prices[today])} on {today}, "
            f"not at the listing's price of {euro(offer.price)}.",
        )

    if sale_started is None:
        start = current_price_since(prices)
    else:
        start = sale_started
        if not first <= start <= today:
            return cannot_check(RULE, f"{start} is outside the price history ({first} to {today}).")
        during = [prices[day] for day in sorted(prices) if day >= start]
        if any(later > earlier for earlier, later in pairwise(during)):
            return cannot_check(
                RULE,
                f"The price went up again after {start}, so this is not one reduction "
                "that grew step by step.",
            )

    window = [start - timedelta(days=n) for n in range(DAYS, 0, -1)]
    known = {day: prices[day] for day in window if day in prices}
    facts = {
        "price": offer.price,
        "was_price": offer.was_price,
        "discount_percent": offer.discount_percent,
        "sale_started": start,
        "window_start": window[0],
        "window_end": window[-1],
        "days_with_a_price": len(known),
    }
    if not known:
        if sale_started is None and start == first:
            reason = (
                f"The price has been {euro(offer.price)} since the price history starts on "
                f"{first}, so the history doesn't show the 30 days before the sale."
            )
        else:
            reason = f"The price history has no prices for the 30 days before {start}."
        return cannot_check(RULE, reason, **facts)

    # A lower price on any known day is enough to show a problem, even when some
    # days are missing: the true lowest price can only be lower still.
    lowest = min(known.values())
    real = percent_below(lowest, offer.price)
    facts |= {
        "lowest_price": lowest,
        "real_discount_percent": real.quantize(Decimal("0.1"), ROUND_HALF_UP),
    }

    findings = []
    if offer.was_price > lowest:
        days_at_was = sum(price == offer.was_price for price in known.values())
        message = (
            f"The crossed-out price {euro(offer.was_price)} is higher than {euro(lowest)}, "
            f"the lowest price of the 30 days before the sale ({window[0]} to {window[-1]})."
        )
        if days_at_was:
            message += f" {euro(offer.was_price)} was charged on only {days_at_was} of them."
        else:
            message += f" {euro(offer.was_price)} wasn't charged on any of them."
        findings.append(
            Finding(
                code="was_price_too_high",
                message=message,
                facts={"lowest_price": lowest, "days_at_was_price": days_at_was},
            )
        )
    if offer.discount_percent is not None:
        # A badge may round the real discount to the nearest whole percent
        allowed = max(real, Decimal(0)).quantize(Decimal(1), ROUND_HALF_UP)
        if offer.discount_percent > allowed:
            below = f"only {real:.1f}% below" if real > 0 else "not below"
            findings.append(
                Finding(
                    code="badge_too_high",
                    message=(
                        f"The badge says -{offer.discount_percent}%, but the price is {below} "
                        f"{euro(lowest)}, the lowest price of the 30 days before the sale."
                    ),
                    facts={"badge_percent": offer.discount_percent, "lowest_price": lowest},
                )
            )
    if findings:
        return problem(RULE, findings, **facts)

    if len(known) < DAYS:
        return cannot_check(
            RULE,
            f"The price history covers only {len(known)} of the 30 days before the sale, "
            "and none of them had a lower price.",
            **facts,
        )
    if offer.was_price == lowest:
        summary = (
            f"The crossed-out price {euro(offer.was_price)} is the lowest price of the "
            f"30 days before the sale started on {start}."
        )
    else:
        summary = (
            f"The crossed-out price {euro(offer.was_price)} is below the lowest price of "
            f"the 30 days before the sale ({euro(lowest)}), so it doesn't overstate the "
            "discount."
        )
    if offer.discount_percent is not None:
        summary += f" The badge -{offer.discount_percent}% fits a {real:.1f}% reduction."
    return ok(RULE, summary, **facts)
