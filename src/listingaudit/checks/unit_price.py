"""Price per kilogram or litre (PAngV § 4 and § 5; Directive 98/6/EC Art. 3).

Packed goods sold by weight or volume show the price per 1 kg or 1 litre next to
the price. It may be left out when it equals the price (a 1 kg or 1 litre pack),
and goods under 10 g or 10 ml need none. Whether a product is sold by weight or
volume at all, or is a set of different products, is decided before this check.
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from listingaudit.checks.results import (
    CENT,
    CheckResult,
    Finding,
    cannot_check,
    euro,
    is_missing,
    not_applicable,
    ok,
    problem,
)
from listingaudit.market.models import Offer
from listingaudit.market.rules import Rule

RULE = Rule.UNIT_PRICE

# "500 g", "1,5 l", "3 x 250 ml". A comma is a decimal point, as on German labels.
QUANTITY = re.compile(
    r"^\s*(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(kg|g|ml|cl|l)\s*$", re.IGNORECASE
)
# Unit on the label -> unit of the unit price, and the factor to convert to it
UNITS = {
    "g": ("kg", Decimal("0.001")),
    "kg": ("kg", Decimal(1)),
    "ml": ("l", Decimal("0.001")),
    "cl": ("l", Decimal("0.01")),
    "l": ("l", Decimal(1)),
}
# Units a unit price may be shown in, by what is measured
SAME_MEASURE = {"kg": ("kg", "100 g"), "l": ("l", "100 ml")}
UNDER_10_G_OR_ML = Decimal("0.01")


@dataclass(frozen=True)
class Quantity:
    amount: Decimal  # in kg or litres
    unit: Literal["kg", "l"]

    def __str__(self) -> str:
        return f"{self.amount.normalize():f} {self.unit}"


def parse_quantity(text: str) -> Quantity | None:
    """'3 x 250 ml' -> 0.75 l, '1,5 kg' -> 1.5 kg, or None if it can't be read."""
    match = QUANTITY.match(text)
    if not match:
        return None
    count, size, unit = match.groups()
    target, factor = UNITS[unit.lower()]
    return Quantity(int(count or 1) * Decimal(size.replace(",", ".")) * factor, target)


def price_per(price: Decimal, quantity: Quantity, per: str) -> Decimal:
    """The exact price per 'kg', 'l', '100 g' or '100 ml'."""
    units = quantity.amount * 10 if per.startswith("100") else quantity.amount
    return price / units


def check_unit_price(offer: Offer) -> CheckResult:
    if is_missing(offer.net_quantity):
        return cannot_check(
            RULE, "The listing doesn't show its net quantity, so the unit price can't be checked."
        )
    quantity = parse_quantity(offer.net_quantity)
    if quantity is None:
        return cannot_check(RULE, f"The net quantity {offer.net_quantity!r} can't be read.")
    if quantity.amount < UNDER_10_G_OR_ML:
        return not_applicable(
            RULE, f"{offer.net_quantity} is less than 10 g or 10 ml, which needs no unit price."
        )

    unit = quantity.unit
    correct = price_per(offer.price, quantity, unit)
    facts = {
        "price": offer.price,
        "net_quantity": offer.net_quantity,
        "quantity": str(quantity),
        "correct_unit_price": correct.quantize(CENT, ROUND_HALF_UP),
        "correct_per": unit,
        "shown_unit_price": offer.unit_price,
        "shown_per": offer.unit_price_per,
    }

    if offer.unit_price is None:
        if quantity.amount == 1:
            return ok(
                RULE,
                f"The pack holds exactly 1 {unit}, so the unit price equals the price and "
                "may be left out.",
                **facts,
            )
        return problem(
            RULE,
            [
                Finding(
                    code="unit_price_missing",
                    message=(
                        f"No price per {unit} is shown. For {offer.net_quantity} at "
                        f"{euro(offer.price)} it is {euro(correct)} per {unit}."
                    ),
                )
            ],
            **facts,
        )

    shown = offer.unit_price_per
    findings = []
    if shown not in SAME_MEASURE[unit]:
        sold_by = "weight" if unit == "kg" else "volume"
        findings.append(
            Finding(
                code="unit_price_wrong_unit",
                message=(
                    f"The unit price is given per {shown}, but the product is sold by "
                    f"{sold_by}: it should be {euro(correct)} per {unit}."
                ),
            )
        )
    else:
        if shown != unit:
            findings.append(
                Finding(
                    code="unit_price_wrong_unit",
                    message=(
                        f"The unit price is given per {shown}. Packed goods show it per 1 "
                        f"{unit} (100 g and 100 ml are only for loose goods): "
                        f"{euro(correct)} per {unit}."
                    ),
                )
            )
        expected = price_per(offer.price, quantity, shown)
        # Shops may round or cut off the last cent
        if abs(offer.unit_price - expected) >= CENT:
            findings.append(
                Finding(
                    code="unit_price_wrong",
                    message=(
                        f"The unit price {euro(offer.unit_price)} per {shown} doesn't fit the "
                        f"price: {euro(offer.price)} for {offer.net_quantity} is "
                        f"{euro(expected)} per {shown}."
                    ),
                    facts={"expected": expected.quantize(CENT, ROUND_HALF_UP)},
                )
            )
    if findings:
        return problem(RULE, findings, **facts)
    return ok(
        RULE,
        f"The unit price {euro(offer.unit_price)} per {shown} fits {euro(offer.price)} "
        f"for {offer.net_quantity}.",
        **facts,
    )
