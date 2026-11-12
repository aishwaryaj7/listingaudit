"""Run the code checks on marketplace listings.

    uv run python -m listingaudit.checks.run P0042 prior_price unit_price

Prints each result as JSON. Listings are read with the agent's read-only login.
"""

import argparse
from collections import defaultdict
from collections.abc import Callable, Iterable

import psycopg
from psycopg.rows import dict_row

from listingaudit.checks.fibres import check_fibre_composition
from listingaudit.checks.prior_price import check_prior_price
from listingaudit.checks.product_info import (
    check_energy_label,
    check_eu_responsible_person,
    check_manufacturer_info,
    check_safety_warnings,
)
from listingaudit.checks.results import CheckResult
from listingaudit.checks.unit_price import check_unit_price
from listingaudit.db import connect_as_agent
from listingaudit.market.models import Offer, PricePoint
from listingaudit.market.rules import Rule

# Checks that only need the listing. The price check also needs the price history.
LISTING_CHECKS: dict[Rule, Callable[[Offer], CheckResult]] = {
    Rule.MANUFACTURER_INFO: check_manufacturer_info,
    Rule.EU_RESPONSIBLE_PERSON: check_eu_responsible_person,
    Rule.SAFETY_WARNINGS: check_safety_warnings,
    Rule.UNIT_PRICE: check_unit_price,
    Rule.FIBRE_COMPOSITION: check_fibre_composition,
    Rule.ENERGY_LABEL: check_energy_label,
}
CODE_RULES = [rule for rule in Rule if rule == Rule.PRIOR_PRICE or rule in LISTING_CHECKS]


def run_checks(offer: Offer, history: list[PricePoint], rules: Iterable[Rule]) -> list[CheckResult]:
    """The result of each rule's check. Rules without a code check are skipped."""
    results = []
    for rule in rules:
        if rule == Rule.PRIOR_PRICE:
            results.append(check_prior_price(offer, history))
        elif rule in LISTING_CHECKS:
            results.append(LISTING_CHECKS[rule](offer))
    return results


OFFERS_SQL = """
    SELECT * FROM market.offers
    WHERE split = %(split)s OR offer_id = ANY(%(ids)s)
    ORDER BY offer_id
"""
PRICES_SQL = """
    SELECT h.offer_id, h.day, h.price
    FROM market.price_history h JOIN market.offers o USING (offer_id)
    WHERE o.split = %(split)s OR o.offer_id = ANY(%(ids)s)
    ORDER BY h.offer_id, h.day
"""


def load_listings(
    conn: psycopg.Connection, *, split: str | None = None, offer_ids: list[str] | None = None
) -> list[tuple[Offer, list[PricePoint]]]:
    """Listings with their price histories: a whole split, or the given offer ids."""
    params = {"split": split, "ids": offer_ids or []}
    with conn.cursor(row_factory=dict_row) as cur:
        offers = cur.execute(OFFERS_SQL, params).fetchall()
        points = cur.execute(PRICES_SQL, params).fetchall()
    histories = defaultdict(list)
    for point in points:
        histories[point["offer_id"]].append(PricePoint(**point))
    return [(Offer(**row), histories[row["offer_id"]]) for row in offers]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the code checks on one listing.")
    parser.add_argument("offer_id", help="e.g. P0042")
    parser.add_argument("rules", nargs="+", choices=[str(rule) for rule in CODE_RULES])
    args = parser.parse_args()

    with connect_as_agent() as conn:
        listings = load_listings(conn, offer_ids=[args.offer_id])
    if not listings:
        parser.error(f"no listing {args.offer_id}")
    offer, history = listings[0]
    for result in run_checks(offer, history, [Rule(rule) for rule in args.rules]):
        print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
