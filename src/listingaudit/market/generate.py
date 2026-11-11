"""Generate one split (practice or exam) of the marketplace.

All randomness comes from the split's seed, which seeds both NumPy and Faker.
The same seed always builds the same split; a different seed gives a different
split with the same mix of problems.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import numpy as np

from listingaudit.market.catalogue import Photo, Product
from listingaudit.market.companies import CompanyFactory, Directory
from listingaudit.market.listing import PROBLEM_RULE, TRAP_RULE, Context, build_listing
from listingaudit.market.models import Case, Offer, PricePoint, Problem, Seller, Trap, money
from listingaudit.market.plan import plan_split
from listingaudit.market.prices import history_dates
from listingaudit.market.rules import applicable_rules

SPLIT_SIZE = 400
PRODUCTS_PER_KIND = {
    "general": 135,
    "unit_price": 85,
    "textile": 65,
    "light_bulb": 45,
    "lamp": 40,
    "candle": 15,
    "mixed_set": 8,
    "not_textile": 7,
}


@dataclass
class SplitResult:
    offers: list[Offer]
    prices: list[PricePoint]
    sellers: list[Seller]
    cases: list[Case]
    problems: list[Problem]
    traps: list[Trap]
    swapped_photo_items: set[str]  # the products whose photos this split swapped in


def random_streams(seed: int) -> tuple[np.random.Generator, np.random.Generator]:
    """Two independent streams of random numbers from one seed.

    The first chooses the products, the second builds the listings. Keeping them
    apart means a change to how listings are built never changes which products
    a split sells.
    """
    choose, build = np.random.SeedSequence(seed).spawn(2)
    return np.random.default_rng(choose), np.random.default_rng(build)


def choose_products(catalogue: list[Product], seed: int, exclude: set[str]) -> list[Product]:
    """A random sample of products, with a fixed number of each kind."""
    rng, _ = random_streams(seed)
    by_kind = defaultdict(list)
    for product in catalogue:
        if product.item_id not in exclude:
            by_kind[product.kind].append(product)
    chosen = []
    for kind, wanted in PRODUCTS_PER_KIND.items():
        pool = by_kind[kind]
        picked = rng.choice(len(pool), size=min(wanted, len(pool)), replace=False)
        chosen += [pool[i] for i in sorted(picked)]
    if len(chosen) != SPLIT_SIZE:
        raise ValueError(f"Only {len(chosen)} products available, need {SPLIT_SIZE}")
    return chosen


def generate_split(
    products: list[Product],
    photos: list[Photo],
    split: str,
    seed: int,
    snapshot: date,
    first_seller_id: int = 1,
) -> SplitResult:
    """Build the listings of one split.

    `products` are the products the split sells (see choose_products). `photos`
    are the photos it may swap in: none of them may show a product on sale.
    """
    _, rng = random_streams(seed)
    directory = Directory(CompanyFactory(seed, rng), rng)
    photos_by_type = defaultdict(list)
    for photo in photos:
        photos_by_type[photo.product_type].append(photo)
    plans = plan_split(products, photos_by_type, rng)
    ctx = Context(rng, directory, snapshot)
    days = history_dates(snapshot)
    swapped = {plan.photo.item_id for plan in plans if plan.photo}
    result = SplitResult([], [], [], [], [], [], swapped)
    seller_ids: dict[str, int] = {}

    # Listing numbers in random order, so nothing can be read from the number
    for number, i in enumerate(rng.permutation(len(products)), start=1):
        product, plan = products[int(i)], plans[int(i)]
        offer_id = f"{split[0].upper()}{number:04d}"

        seller = directory.seller()
        if seller.name not in seller_ids:
            seller_ids[seller.name] = first_seller_id + len(seller_ids)
            result.sellers.append(
                Seller(
                    seller_id=seller_ids[seller.name],
                    name=seller.name,
                    country=seller.country_name,
                    email=seller.email,
                )
            )

        fields = {"offer_id": offer_id, "split": split, "seller_id": seller_ids[seller.name]}
        listing = build_listing(product, plan, ctx, fields)
        result.offers.append(Offer(**listing.fields))
        result.prices += [
            PricePoint(offer_id=offer_id, day=day, price=money(cents))
            for day, cents in zip(days, listing.story.history, strict=True)
        ]

        kind = "problem" if listing.problems else "tricky" if listing.trap else "clean"
        announces_reduction = listing.story.was_price_kind == "previous_price"
        rules = applicable_rules(product, announces_reduction, listing.maker.in_eu)
        result.cases.append(
            Case(
                offer_id=offer_id,
                split=split,
                kind=kind,
                source_item_id=product.item_id,
                applicable_rules=rules,
                sneaky_note=listing.sneaky_note,
            )
        )
        for name, difficulty, evidence in listing.problems:
            result.problems.append(
                Problem(
                    offer_id=offer_id,
                    problem=name,
                    rule=PROBLEM_RULE[name],
                    difficulty=difficulty,
                    evidence=evidence,
                )
            )
        if listing.trap:
            name, why = listing.trap
            result.traps.append(
                Trap(offer_id=offer_id, trap=name, rule=TRAP_RULE[name], why_legal=why)
            )

    result.offers.sort(key=lambda o: o.offer_id)
    result.cases.sort(key=lambda c: c.offer_id)
    return result
