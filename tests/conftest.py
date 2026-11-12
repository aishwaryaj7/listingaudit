"""Shared test data: a made-up catalogue for the generator tests, and listings for the checks."""

from decimal import Decimal

import pytest

from listingaudit.market.catalogue import PackSize, Photo, Product, colour_family
from listingaudit.market.models import Offer

COLOURS = [
    "Navy Blue", "Ivory", "Charcoal Grey", "Black", "Sage Green",
    "Red", "Beige", "Walnut Brown", None, "Multicolor",
]  # fmt: skip
PHOTO_COLOURS = [
    "Navy Blue", "Ivory", "Charcoal Grey", "Black", "Sage Green",
    "Red", "Beige", "Walnut Brown", "Mustard Yellow", "Blush Pink",
]  # fmt: skip
PACKS = [
    PackSize(1, 250, "g"),
    PackSize(1, 500, "ml"),
    PackSize(2, 250, "ml"),
    PackSize(1, 1000, "g"),  # exactly 1 kg: may leave out the unit price
    PackSize(6, 330, "ml"),
    PackSize(1, 340, "g"),
]
# Kind -> (how many products, their product types). Enough for two splits.
KINDS = {
    "general": (300, ["CHAIR", "SOFA", "TABLE", "HOME_FURNITURE_AND_DECOR", "SHOES"]),
    "unit_price": (
        200,
        [
            "GROCERY",
            "COFFEE",
            "TEA",
            "SNACK_FOOD_BAR",
            "SKIN_CLEANING_AGENT",
            "SKIN_MOISTURIZER",
            "SHAMPOO",
            "CLEANING_AGENT",
        ],
    ),  # fmt: skip
    "textile": (150, ["RUG", "PILLOW", "FLAT_SHEET", "HOME_BED_AND_BATH", "CURTAIN"]),
    "light_bulb": (100, ["LIGHT_BULB"]),
    "lamp": (90, ["LAMP", "LIGHT_FIXTURE", "HOME_LIGHTING_AND_LAMPS"]),
    "candle": (40, ["CANDLE"]),
    "mixed_set": (20, ["SHAMPOO", "SKIN_MOISTURIZER"]),
    "not_textile": (16, ["CURTAIN"]),
}


def made_up_product(n: int, kind: str, product_type: str) -> Product:
    color = COLOURS[n % len(COLOURS)]
    title = f"Test {product_type.replace('_', ' ').title()} {n}"
    pack = fibres = None
    if kind == "unit_price":
        pack = PACKS[n % len(PACKS)]
        title += f", {pack}"
    if kind == "candle" and n % 3 == 0:
        title += ", 8 oz"  # a weight in the title, but candles are sold by the piece
    if kind == "textile" and n % 2 == 0:
        fibres = "80% cotton, 20% polyester"
    return Product(
        item_id=f"ITEM{n:04d}",
        product_type=product_type,
        kind=kind,
        title=title,
        brand="Testbrand",
        color=color,
        bullet_points=["A first bullet point.", "A second bullet point."],
        description="A product made up for the tests.",
        image_id=f"img{n:04d}",
        image_file=f"images/test/{n:04d}.jpg",
        colour_family=colour_family(color),
        pack=pack,
        fibres=fibres,
    )


def made_up_catalogue() -> tuple[list[Product], list[Photo]]:
    """Products of every kind, plus photos of other products to swap in."""
    products = []
    for kind, (count, types) in KINDS.items():
        for i in range(count):
            products.append(made_up_product(len(products), kind, types[i % len(types)]))
    types = sorted({p.product_type for p in products})
    photos = [
        Photo(
            f"PHOTO-{t}-{j}", t, f"photo-{t}-{j}", f"images/test/{t}-{j}.jpg", c, colour_family(c)
        )
        for t in types
        for j, c in enumerate(PHOTO_COLOURS * 2)
    ]
    return products, photos


@pytest.fixture(scope="session")
def made_up():
    return made_up_catalogue()


@pytest.fixture
def make_offer():
    """Build a complete, plain listing; keyword arguments change its fields."""

    def make(**changes) -> Offer:
        fields = {
            "offer_id": "P0001",
            "split": "practice",
            "seller_id": 1,
            "product_type": "PILLOW",
            "title": "Test Pillow",
            "brand": "Testbrand",
            "color": "Navy Blue",
            "bullet_points": ["A first bullet point."],
            "description": "A product made up for the tests.",
            "image_id": "img0001",
            "image_file": "images/test/0001.jpg",
            "price": Decimal("19.99"),
            "manufacturer_name": "Testmaker GmbH",
            "manufacturer_address": "Hauptstraße 1, 10115 Berlin, Germany",
            "manufacturer_country": "Germany",
            "manufacturer_email": "info@testmaker.example",
        }
        return Offer(**(fields | changes))

    return make
