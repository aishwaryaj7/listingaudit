"""The rules of the pretend marketplace, in one place.

Simplified versions of the EU and German rules in the project plan. The generator
uses them to build listings that follow the rules, and the answer key stores
which rules apply to each listing, so we can later check that the agent picked
the right ones. Step 3 turns these into a proper rulebook with the law texts.
"""

from enum import StrEnum

from listingaudit.market.catalogue import Product


class Rule(StrEnum):
    PRIOR_PRICE = "prior_price"
    MANUFACTURER_INFO = "manufacturer_info"
    EU_RESPONSIBLE_PERSON = "eu_responsible_person"
    SAFETY_WARNINGS = "safety_warnings"
    UNIT_PRICE = "unit_price"
    FIBRE_COMPOSITION = "fibre_composition"
    ENERGY_LABEL = "energy_label"
    PHOTO_MATCHES_TEXT = "photo_matches_text"


# What each rule says, in plain words, and where it comes from
RULES: dict[Rule, tuple[str, str]] = {
    Rule.PRIOR_PRICE: (
        "A listing that announces a price reduction shows, as the 'was' price, the lowest "
        "price of the 30 days before the reduction started, and any % badge is calculated "
        "from that price.",
        "Directive 98/6/EC Art. 6a; PAngV § 11; EU Court of Justice C-330/23 (Aldi Süd)",
    ),
    Rule.MANUFACTURER_INFO: (
        "A non-food product shows the manufacturer's name, postal address and an email or "
        "website. (Food is outside the GPSR.)",
        "GPSR (EU) 2023/988 Art. 19(a), Art. 2(2)",
    ),
    Rule.EU_RESPONSIBLE_PERSON: (
        "If the manufacturer is not in the EU, the listing also shows a responsible person "
        "based in the EU, with postal address and email.",
        "GPSR (EU) 2023/988 Art. 19(b), Art. 16",
    ),
    Rule.SAFETY_WARNINGS: (
        "Candles, electric products, cleaning products and cosmetics show their safety warnings.",
        "GPSR (EU) 2023/988 Art. 19(d)",
    ),
    Rule.UNIT_PRICE: (
        "Goods sold by weight or volume show the price per 1 kg or 1 litre next to the "
        "price (not per 100 g). It may be left out when it equals the price (a 1 kg pack). "
        "Sets of different products are exempt.",
        "PAngV § 4, § 5",
    ),
    Rule.FIBRE_COMPOSITION: (
        "A textile product shows its fibre composition, using EU fibre names with "
        "percentages (e.g. '60% cotton, 40% viscose', not 'rayon' or 'cotton feel').",
        "Textile Regulation (EU) 1007/2011 Art. 5, 9, 16 and Annex I",
    ),
    Rule.ENERGY_LABEL: (
        "A light bulb sold on its own shows its energy label near the price and links its "
        "product information sheet. Lamps and light fittings have no label of their own "
        "since 2021.",
        "Energy Labelling Regulation (EU) 2017/1369; Delegated Regulation (EU) 2019/2015",
    ),
    Rule.PHOTO_MATCHES_TEXT: (
        "The photo shows the product the text describes (colour, material, type).",
        "Unfair Commercial Practices Directive 2005/29/EC Art. 6",
    ),
}

FOOD_TYPES = {"GROCERY", "COFFEE", "TEA", "SNACK_FOOD_BAR"}
# Product types that carry safety warnings besides candles and electric products
WARNING_TYPES = {"CLEANING_AGENT", "SKIN_CLEANING_AGENT", "SKIN_MOISTURIZER", "SHAMPOO"}


def is_food(product: Product) -> bool:
    return product.product_type in FOOD_TYPES


def needs_warnings(product: Product) -> bool:
    return product.kind in ("candle", "light_bulb", "lamp") or product.product_type in WARNING_TYPES


def applicable_rules(product: Product, announces_reduction: bool, maker_in_eu: bool) -> list[Rule]:
    """The rules a listing has to follow, in the order of `Rule`."""
    applies = {
        Rule.PRIOR_PRICE: announces_reduction,
        Rule.MANUFACTURER_INFO: not is_food(product),
        Rule.EU_RESPONSIBLE_PERSON: not is_food(product) and not maker_in_eu,
        Rule.SAFETY_WARNINGS: needs_warnings(product),
        Rule.UNIT_PRICE: product.kind == "unit_price",
        Rule.FIBRE_COMPOSITION: product.kind == "textile",
        Rule.ENERGY_LABEL: product.kind == "light_bulb",
        Rule.PHOTO_MATCHES_TEXT: True,
    }
    return [rule for rule in Rule if applies[rule]]
