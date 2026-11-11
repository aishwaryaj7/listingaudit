"""Whole splits: the same seed gives the same data, and the answer key is complete.

These run on a made-up catalogue, and also on the real ABO products when the
database is available.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from urllib.parse import urlparse

import psycopg
import pytest

from listingaudit.db import connect
from listingaudit.market.build import SEEDS, SNAPSHOT_DATE, build_splits
from listingaudit.market.catalogue import Product, load_catalogue, normalise_composition
from listingaudit.market.companies import EU
from listingaudit.market.generate import SPLIT_SIZE
from listingaudit.market.listing import WARNINGS, unit_price, warning_category
from listingaudit.market.models import Case, Offer, Problem, Trap
from listingaudit.market.plan import PROBLEM_COUNTS, PROBLEM_LISTINGS, SNEAKY_NOTES, TRAP_COUNTS
from listingaudit.market.prices import HISTORY_DAYS, RULE_DAYS, percent_off
from listingaudit.market.rules import Rule, is_food, needs_warnings

EU_COUNTRIES = {name for name, _ in EU.values()}


@pytest.fixture(scope="module", params=["made_up", "abo"])
def catalogue(request):
    if request.param == "made_up":
        return request.getfixturevalue("made_up")
    try:
        with connect() as conn:
            products, photos = load_catalogue(conn)
    except psycopg.Error:
        pytest.skip("the database is not running")
    if not products:
        pytest.skip("no ABO products in the database")
    return products, photos


@pytest.fixture(scope="module")
def splits(catalogue):
    return build_splits(*catalogue)


@pytest.fixture(scope="module")
def products(catalogue):
    return {p.item_id: p for p in catalogue[0]}


def dump(result) -> list:
    parts = ["offers", "prices", "sellers", "cases", "problems", "traps"]
    return [[row.model_dump() for row in getattr(result, part)] for part in parts]


# --- Same seed, same data --------------------------------------------------------------


def test_the_same_seeds_give_the_same_splits(catalogue, splits):
    again = build_splits(*catalogue)
    for split in SEEDS:
        assert dump(again[split]) == dump(splits[split])


def test_other_seeds_give_other_splits(catalogue, splits):
    other = build_splits(*catalogue, seeds={"practice": 1, "exam": 2})
    for split in SEEDS:
        assert {o.title for o in other[split].offers} != {o.title for o in splits[split].offers}


# --- The planned mix ----------------------------------------------------------------------


@pytest.mark.parametrize("split", list(SEEDS))
def test_each_split_has_the_planned_mix(splits, split):
    result = splits[split]
    assert len(result.offers) == SPLIT_SIZE
    assert Counter(p.problem for p in result.problems) == PROBLEM_COUNTS
    assert sum(case.kind == "problem" for case in result.cases) == PROBLEM_LISTINGS
    traps = Counter(t.trap for t in result.traps)
    assert {trap: traps[trap] for trap in TRAP_COUNTS} == TRAP_COUNTS
    assert sum(case.sneaky_note is not None for case in result.cases) == SNEAKY_NOTES


@pytest.mark.parametrize("split", list(SEEDS))
def test_every_problem_comes_half_easy_half_hard(splits, split):
    mix = Counter((p.problem, p.difficulty) for p in splits[split].problems)
    for problem, count in PROBLEM_COUNTS.items():
        if problem == "photo_mismatch":  # hard needs a photo in a similar colour
            assert abs(mix[problem, "easy"] - mix[problem, "hard"]) <= 6
        else:
            assert mix[problem, "easy"] == mix[problem, "hard"] == count // 2


def test_kinds_match_the_answer_key(splits):
    for result in splits.values():
        with_problem = {p.offer_id for p in result.problems}
        with_trap = {t.offer_id for t in result.traps}
        assert not with_problem & with_trap  # a problem or a trap, never both
        for case in result.cases:
            if case.offer_id in with_problem:
                assert case.kind == "problem"
            elif case.offer_id in with_trap:
                assert case.kind == "tricky"
            else:
                assert case.kind == "clean"


def test_planted_problems_break_rules_that_apply(splits):
    for result in splits.values():
        rules = {case.offer_id: case.applicable_rules for case in result.cases}
        for problem in result.problems:
            assert problem.rule in rules[problem.offer_id]


def test_sneaky_notes_hide_in_problem_listings(splits):
    for result in splits.values():
        offers = {o.offer_id: o for o in result.offers}
        for case in result.cases:
            if case.sneaky_note:
                offer = offers[case.offer_id]
                assert case.kind == "problem"
                text = [offer.description or "", *offer.bullet_points]
                assert any(case.sneaky_note in part for part in text)


# --- The answer key is right --------------------------------------------------------------


def cents(amount: Decimal) -> int:
    return int(amount * 100)


def broken_rules(offer: Offer, product: Product, history: list[Decimal]) -> set[Rule]:
    """Check a listing against every rule, using only what the listing shows.

    Deliberately independent of the generator: it reads the price history, the
    price box and the safety information the way a careful human checker would,
    without knowing how the listing was made.
    """
    broken = set()
    prices = [cents(price) for price in history]
    if offer.was_price_kind == "previous_price":
        start = len(prices) - 1  # the day the reduced price began
        while prices[start - 1] == prices[-1]:
            start -= 1
        lowest = min(prices[start - RULE_DAYS : start])
        was, now = cents(offer.was_price), cents(offer.price)
        if was != lowest or offer.discount_percent != percent_off(was, now):
            broken.add(Rule.PRIOR_PRICE)

    if not is_food(product):
        contact = offer.manufacturer_email or offer.manufacturer_website
        if not (offer.manufacturer_name and offer.manufacturer_address and contact):
            broken.add(Rule.MANUFACTURER_INFO)
        if offer.manufacturer_country not in EU_COUNTRIES:
            rp_in_eu = (offer.eu_rp_address or "").endswith(tuple(EU_COUNTRIES))
            if not (offer.eu_rp_name and rp_in_eu and offer.eu_rp_email):
                broken.add(Rule.EU_RESPONSIBLE_PERSON)

    if needs_warnings(product):
        shown = offer.safety_warnings or ""
        if not any(warning in shown for warning in WARNINGS[warning_category(product)]):
            broken.add(Rule.SAFETY_WARNINGS)

    if product.kind == "unit_price":
        pack = product.pack
        shown = (offer.unit_price, offer.unit_price_per)
        left_out_at_one_kilo = pack.total == 1000 and shown == (None, None)
        correct = unit_price(cents(offer.price), pack.total, pack.unit)
        if shown != correct and not left_out_at_one_kilo:
            broken.add(Rule.UNIT_PRICE)

    if product.kind == "textile":
        shown = offer.fibre_composition
        if not shown or normalise_composition(shown) != shown:
            broken.add(Rule.FIBRE_COMPOSITION)

    if product.kind == "light_bulb":
        if not (offer.energy_class and offer.energy_label_url and offer.product_sheet_url):
            broken.add(Rule.ENERGY_LABEL)

    if offer.image_id != product.image_id:
        broken.add(Rule.PHOTO_MATCHES_TEXT)
    return broken


def test_listings_break_exactly_the_planted_rules(splits, products):
    """The answer key must match the listings exactly.

    A rule break the answer key doesn't know about would mark the agent wrong for
    finding it; a planted problem the listing doesn't really show would ask the
    agent to find something that isn't there.
    """
    for result in splits.values():
        histories = defaultdict(list)
        for point in result.prices:
            histories[point.offer_id].append(point.price)
        planted = defaultdict(set)
        for problem in result.problems:
            planted[problem.offer_id].add(problem.rule)
        sources = {case.offer_id: case.source_item_id for case in result.cases}
        for offer in result.offers:
            product = products[sources[offer.offer_id]]
            found = broken_rules(offer, product, histories[offer.offer_id])
            assert found == planted[offer.offer_id], offer.offer_id


def test_price_histories_end_today_at_the_shown_price(splits):
    for result in splits.values():
        offers = {o.offer_id: o for o in result.offers}
        histories = defaultdict(list)
        for point in result.prices:
            histories[point.offer_id].append(point)
        assert histories.keys() == offers.keys()
        for offer_id, points in histories.items():
            assert len(points) == HISTORY_DAYS
            assert points[-1].day == SNAPSHOT_DATE
            assert points[-1].price == offers[offer_id].price


# --- Nothing leaks ----------------------------------------------------------------------------


def test_offers_hold_nothing_from_the_answer_key():
    answer_key = set(Case.model_fields) | set(Problem.model_fields) | set(Trap.model_fields)
    assert set(Offer.model_fields) & answer_key == {"offer_id", "split"}


def test_splits_share_no_products_or_photos(splits):
    def shown(result) -> set[str]:
        items = {case.source_item_id for case in result.cases}
        swapped = {
            p.evidence["photo_from_item"] for p in result.problems if p.problem == "photo_mismatch"
        }
        return items | swapped

    assert not shown(splits["practice"]) & shown(splits["exam"])
    photos = [o.image_id for result in splits.values() for o in result.offers]
    assert len(photos) == len(set(photos))  # no photo appears twice


def test_ids_are_unique_across_splits(splits):
    offer_ids = [o.offer_id for result in splits.values() for o in result.offers]
    seller_ids = [s.seller_id for result in splits.values() for s in result.sellers]
    assert len(offer_ids) == len(set(offer_ids))
    assert len(seller_ids) == len(set(seller_ids))


def test_all_contact_details_are_fictional(splits):
    # ".example" is reserved (RFC 2606): no real company can ever own these addresses
    for result in splits.values():
        addresses = [s.email for s in result.sellers]
        for o in result.offers:
            addresses += [o.manufacturer_email, o.manufacturer_website, o.eu_rp_email]
            addresses += [o.energy_label_url, o.product_sheet_url]
        for address in filter(None, addresses):
            host = urlparse(address).hostname if "://" in address else address.split("@")[1]
            assert host.endswith(".example"), address
