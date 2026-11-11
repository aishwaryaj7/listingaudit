"""Decide which listings get which problems and traps.

The number of listings per problem is fixed up front. Problems are then handed
out to products they fit: a missing fibre composition only fits a textile, a
missing energy label only a light bulb.
"""

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from listingaudit.market.catalogue import NEAR_COLOURS, SIZE, Photo, Product
from listingaudit.market.rules import is_food

# How many listings of a split get each problem. 220 listings get at least one
# problem; those beyond 220 are second problems on the same listings.
PROBLEM_COUNTS = {
    # These fit only some kinds of products, so they are handed out first
    "missing_energy_label": 20,
    "bad_fibre_composition": 22,
    "bad_unit_price": 30,
    "missing_safety_warnings": 20,
    # These fit (almost) any product
    "pumped_price": 30,
    "sneaky_percentage": 30,
    "missing_manufacturer_info": 30,
    "missing_eu_responsible_person": 30,
    "photo_mismatch": 30,
}
PROBLEM_LISTINGS = 220
NARROW_PROBLEMS = list(PROBLEM_COUNTS)[:4]
WIDE_PROBLEMS = list(PROBLEM_COUNTS)[4:]
# At most one problem from each group per listing
CLASHES = [
    {"pumped_price", "sneaky_percentage"},
    {"missing_manufacturer_info", "missing_eu_responsible_person"},
]

# Traps handed out to a fixed number of listings (some products are traps by nature,
# see natural_trap)
TRAP_COUNTS = {
    "lamp_no_energy_label": 10,
    "eu_maker_no_rp_needed": 10,
    "long_price_rise": 10,
    "sale_started_earlier": 10,
    "rrp_comparison": 10,
    "unannounced_price_drop": 6,
}
SNEAKY_NOTES = 20

# Photos are only swapped between products that look alike, so the wrong photo shows
# the same kind of product in another colour. (HOME_FURNITURE_AND_DECOR, for example,
# mixes ottomans and photo albums.)
SWAP_TYPES = {
    "CHAIR", "SOFA", "TABLE", "SHOES", "RUG", "PILLOW", "CURTAIN", "FLAT_SHEET",
    "LAMP", "LIGHT_FIXTURE", "LIGHT_BULB", "CANDLE",
}  # fmt: skip


@dataclass
class Plan:
    problems: list[str] = field(default_factory=list)
    hard: dict[str, bool] = field(default_factory=dict)  # problem -> hard version?
    trap: str | None = None
    sneaky_note: bool = False
    photo: Photo | None = None  # for a photo_mismatch: the other product's photo

    @property
    def is_free(self) -> bool:
        return not self.problems and self.trap is None


def natural_trap(product: Product) -> str | None:
    """The trap a product is by nature: it looks like it breaks a rule but doesn't."""
    if product.kind == "mixed_set":
        return "mixed_set"
    if product.kind == "not_textile":
        return "not_a_textile"
    if product.kind == "unit_price" and product.pack.total == 1000:
        return "exact_kilo_or_litre"
    if product.kind == "candle" and SIZE.search(product.title):
        return "sold_by_piece"
    return None


def fits(name: str, product: Product, photos: dict[str, list[Photo]]) -> bool:
    """Can this problem or trap be planted on this product?"""
    match name:
        case "missing_energy_label":
            return product.kind == "light_bulb"
        case "bad_fibre_composition":
            # Only textiles whose own text states no composition: otherwise the
            # composition would still be there, in the description
            return product.kind == "textile" and product.fibres is None
        case "bad_unit_price":
            return product.kind == "unit_price"
        case "missing_safety_warnings":
            return product.kind in ("candle", "light_bulb", "lamp")
        case (
            "missing_manufacturer_info" | "missing_eu_responsible_person" | "eu_maker_no_rp_needed"
        ):
            return not is_food(product)
        case "photo_mismatch":
            return (
                product.product_type in SWAP_TYPES
                and product.colour_family is not None
                and any(
                    photo.colour_family != product.colour_family
                    for photo in photos.get(product.product_type, [])
                )
            )
        case "lamp_no_energy_label":
            return product.kind == "lamp"
        case _:  # price problems and price traps fit any product
            return True


def reserve_photo(
    product: Product, photos: dict[str, list[Photo]], want_near: bool, rng
) -> tuple[Photo, bool]:
    """Set aside a photo of another product of the same type, in another colour.

    A colour that is easy to confuse (beige for white) makes a hard case and is
    preferred when `want_near` is set. The photo is removed from `photos`, so no
    other listing gets it and `fits` only sees the photos that are still free.
    Returns the photo and whether its colour is a near one.
    """
    options = [
        ph for ph in photos[product.product_type] if ph.colour_family != product.colour_family
    ]
    near = [
        ph for ph in options if frozenset((ph.colour_family, product.colour_family)) in NEAR_COLOURS
    ]
    far = [ph for ph in options if ph not in near]
    pool = (near or far) if want_near else (far or near)
    photo = pool[int(rng.integers(len(pool)))]
    photos[product.product_type].remove(photo)
    return photo, photo in near


def _add_wide_problem(plan: Plan, product, photos, counts: Counter, rng) -> bool:
    """Give the listing the wide problem that is furthest from its target count."""
    options = [
        p
        for p in WIDE_PROBLEMS
        if counts[p] < PROBLEM_COUNTS[p]
        and p not in plan.problems
        and fits(p, product, photos)
        and not any(p in group and set(plan.problems) & group for group in CLASHES)
    ]
    if not options:
        return False
    missing = np.array([PROBLEM_COUNTS[p] - counts[p] for p in options])
    best = [p for p, m in zip(options, missing, strict=True) if m == missing.max()]
    choice = best[int(rng.integers(len(best)))]
    plan.problems.append(choice)
    counts[choice] += 1
    if choice == "photo_mismatch":
        # Hard means the colours are easy to confuse. Ask for one whenever hard
        # swaps aren't ahead, so easy and hard stay even where photos allow.
        want_near = counts["photo_hard"] <= counts["photo_easy"]
        plan.photo, near = reserve_photo(product, photos, want_near, rng)
        plan.hard[choice] = near
        counts["photo_hard" if near else "photo_easy"] += 1
    return True


def plan_split(
    products: list[Product], photos: dict[str, list[Photo]], rng: np.random.Generator
) -> list[Plan]:
    """`photos` holds the swappable photos by product type; it is not modified."""
    photos = {product_type: list(options) for product_type, options in photos.items()}
    plans = [Plan(trap=natural_trap(p)) for p in products]
    order = [int(i) for i in rng.permutation(len(products))]
    counts: Counter = Counter()

    # 1. Problems that only fit some products, then the traps
    for problem in NARROW_PROBLEMS:
        for i in order:
            if counts[problem] < PROBLEM_COUNTS[problem] and plans[i].is_free:
                if fits(problem, products[i], photos):
                    plans[i].problems.append(problem)
                    counts[problem] += 1
    for trap, wanted in TRAP_COUNTS.items():
        given = 0
        for i in order:
            if given < wanted and plans[i].is_free and fits(trap, products[i], photos):
                plans[i].trap = trap
                given += 1

    # 2. One wide problem each for more listings, until 220 listings have a problem
    with_problem = sum(bool(p.problems) for p in plans)
    for i in order:
        if with_problem >= PROBLEM_LISTINGS:
            break
        if plans[i].is_free and _add_wide_problem(plans[i], products[i], photos, counts, rng):
            with_problem += 1

    # 3. Second problems on those listings, until every problem reaches its count
    problem_rows = [i for i in order if plans[i].problems]
    added = True
    while added and any(counts[p] < PROBLEM_COUNTS[p] for p in WIDE_PROBLEMS):
        added = False
        for i in problem_rows:
            if len(plans[i].problems) < 2:
                added |= _add_wide_problem(plans[i], products[i], photos, counts, rng)

    # 4. Exactly half of each problem's listings get the hard version, so every
    #    split has the same mix (photo swaps were decided when the photo was chosen)
    for problem in PROBLEM_COUNTS:
        if problem != "photo_mismatch":
            rows = [i for i in order if problem in plans[i].problems]
            for n, i in enumerate(rng.permutation(rows)):
                plans[int(i)].hard[problem] = n < len(rows) // 2

    # 5. Notes that try to talk the agent out of its job, on listings with a problem
    for i in rng.choice(problem_rows, size=SNEAKY_NOTES, replace=False):
        plans[int(i)].sneaky_note = True
    return plans
