"""ABO product types grouped by the rule that applies to them.

Only these types are sampled. Most of ABO falls under none of the
product-specific rules (phone cases alone are 44% of the listings).
"""

RULE_GROUPS: dict[str, list[str]] = {
    # Textile Regulation: fibre composition must be shown
    "textile": ["RUG", "PILLOW", "FLAT_SHEET", "HOME_BED_AND_BATH", "CURTAIN"],
    # Energy labelling: light bulbs sold on their own must show their energy label.
    # Lamps have had no label of their own since 2021 (kept as negative cases).
    "energy_label": [
        "LAMP",
        "LIGHT_BULB",
        "LIGHT_FIXTURE",
        "HOME_LIGHTING_AND_LAMPS",
        "STRING_LIGHT",
    ],
    # Unit pricing: goods sold by weight or volume need a price per kg / litre
    "unit_price": [
        "GROCERY",
        "COFFEE",
        "TEA",
        "SNACK_FOOD_BAR",
        "SKIN_CLEANING_AGENT",
        "SKIN_MOISTURIZER",
        "SHAMPOO",
        "CLEANING_AGENT",
    ],
    # Products that usually need safety warnings
    "warnings": ["CANDLE"],
    # Only the general product safety info (GPSR) applies
    "general": ["CHAIR", "SOFA", "TABLE", "HOME_FURNITURE_AND_DECOR", "SHOES"],
}

FOCUS_PRODUCT_TYPES = sorted({t for types in RULE_GROUPS.values() for t in types})


def rule_group(product_type: str | None) -> str | None:
    for group, types in RULE_GROUPS.items():
        if product_type in types:
            return group
    return None
