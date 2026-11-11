"""Reading facts from real product text: pack sizes, colours, fibres and kinds."""

from types import SimpleNamespace

import pytest

from listingaudit.market.catalogue import (
    PackSize,
    classify,
    colour_family,
    is_mixed_set,
    make_product,
    normalise_composition,
    pack_size,
)


@pytest.mark.parametrize(
    ("title", "pack"),
    [
        ("Hand Wash, 250 ml (Pack of 2)", PackSize(2, 250, "ml")),
        ("Organic Vanilla Coffee, 12 oz", PackSize(1, 340, "g")),  # ounces of weight
        ("Jojoba Oil, 4 oz", PackSize(1, 118, "ml")),  # on an oil, ounces mean fluid ounces
        ("Facial Toner, 1.7 Fluid Ounce (50 mL)", PackSize(1, 50, "ml")),  # the metric size wins
        ("Olive Oil 500ml x 6 Bottles", PackSize(6, 500, "ml")),
        ("Tuna in Water, 5 oz (Pack of 4)", PackSize(4, 142, "g")),  # tuna is not a liquid
        ("Marinara Sauce, 24 oz", PackSize(1, 680, "g")),  # jars of sauce are sold by weight
    ],
)
def test_pack_sizes_are_read_from_the_title(title, pack):
    assert pack_size(title) == pack


@pytest.mark.parametrize(
    "title",
    [
        "Tea Pods, 24 Ct.",  # a count, no size
        "Sparkling Water, 12 Fl Oz, 12 Count",  # is the size per can or for all of them?
        "Peanut Butter, 16 oz and 32 oz",  # two different sizes
        "Laundry Detergent, 2-1 Gal",  # not a readable size
        "Throw Pillow Cover",  # no size at all
    ],
)
def test_unclear_pack_sizes_give_none(title):
    assert pack_size(title) is None


def test_pack_size_text_looks_like_a_label():
    assert str(PackSize(2, 250, "ml")) == "2 x 250 ml"
    assert str(PackSize(1, 340, "g")) == "340 g"


@pytest.mark.parametrize(
    ("color", "family"),
    [
        ("Navy Blue", "blue"),
        ("Ash Gray", "grey"),
        ("Ivory", "white"),
        ("Black/White", None),  # two families
        ("Multicolor", None),
        ("Unknown Shade", None),
        (None, None),
    ],
)
def test_colour_families(color, family):
    assert colour_family(color) == family


@pytest.mark.parametrize(
    ("fabric", "composition"),
    [
        ("80% Wool, 20% Cotton", "80% wool, 20% cotton"),
        ("100% Microfiber", "100% polyester"),  # microfiber is not an EU fibre name
        (
            "Cover: 63% Linen, 37% Rayon; Insert: 100% Cotton",
            "cover: 63% linen, 37% viscose; insert: 100% cotton",
        ),
        ("50% cotton, 40% polyester", None),  # doesn't add up to 100%
        ("100% Cotton Duck", None),  # unknown words
        ("Cotton", None),  # no percentages
        (None, None),
    ],
)
def test_fibre_compositions_use_eu_fibre_names(fabric, composition):
    assert normalise_composition(fabric) == composition


@pytest.mark.parametrize(
    ("title", "mixed"),
    [
        ("Shampoo, 28 fl oz & Conditioner, 28 fl oz", True),
        ("Coconut Lip Balm Duo", True),
        ("Mac & Cheese, 6 oz", False),  # one product with "&" in its name
        ("Hand Soap, 250 ml & Hand Soap Refill, 1 l", False),  # the same product twice
    ],
)
def test_sets_of_different_products(title, mixed):
    assert is_mixed_set(title) is mixed


@pytest.mark.parametrize(
    ("product_type", "text", "kind"),
    [
        ("RUG", "Soft Rug", "textile"),
        ("CURTAIN", "Curtain Rod Set, Black", "not_textile"),
        ("PILLOW", "Memory Foam Pillow", None),  # foam: is it a textile product? Unclear
        ("LIGHT_BULB", "LED Bulb E27", "light_bulb"),
        ("LAMP", "Table Lamp, takes one E27 bulb", "lamp"),
        ("LAMP", "Table Lamp with built-in LED", None),  # may count as a light source
        ("GROCERY", "Basmati Rice, 2 lb", "unit_price"),
        ("SHAMPOO", "Shampoo, 28 fl oz & Conditioner, 28 fl oz", "mixed_set"),
        ("SKIN_MOISTURIZER", "Lip Balm, 0.15 oz", None),  # under 10 g: no unit price needed
        ("CANDLE", "Scented Candle, 8 oz", "candle"),
        ("CANDLE", "Glass Candle Holder", None),  # not a candle
        ("SOFA", "Two-Seater Sofa", "general"),
        ("CELLULAR_PHONE_CASE", "Phone Case", None),  # not a focus product type
    ],
)
def test_products_are_sorted_into_kinds(product_type, text, kind):
    assert classify(product_type, text, text) == kind


def row(**fields):
    """A row as load_catalogue reads it from the database."""
    defaults = {
        "item_id": "B0TEST0001",
        "product_type": "PILLOW",
        "item_name": "Throw Pillow, Navy Blue",
        "brand": "Testbrand",
        "color": "Navy Blue",
        "fabric_type": None,
        "bullet_points": ["Soft and cosy"],
        "description": None,
        "main_image_id": "81test",
        "local_file": "images/small/81/81test.jpg",
    }
    return SimpleNamespace(**(defaults | fields))


def test_make_product_reads_the_facts():
    product = make_product(row(fabric_type="100% Cotton"))
    assert product.kind == "textile"
    assert product.fibres == "100% cotton"
    assert product.colour_family == "blue"


def test_textile_with_unreadable_composition_is_left_out():
    # States a composition that can't be parsed. A wrong guess would put a rule
    # break into the data that the answer key doesn't know about.
    assert make_product(row(fabric_type="60% Cotton Blend, 40% Other")) is None


def test_products_of_other_types_are_left_out():
    assert make_product(row(product_type="CELLULAR_PHONE_CASE")) is None
