"""Which ABO product types matter for which rule.

We don't need all 147k listings. Phone cases alone are 44% of ABO, and none of
our special rules apply to them. These groups decide which products we keep
photos for and, later, which rules the agent should check.
"""

RULE_GROUPS: dict[str, list[str]] = {
    # Textile Regulation: fibre composition must be shown
    "textile": ["RUG", "PILLOW", "FLAT_SHEET", "HOME_BED_AND_BATH", "CURTAIN"],
    # Energy labelling: lamps and light sources must show their energy label
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
    # Everything else still needs the general product safety info (GPSR)
    "general": ["CHAIR", "SOFA", "TABLE", "HOME_FURNITURE_AND_DECOR", "SHOES"],
}

FOCUS_PRODUCT_TYPES = sorted({t for types in RULE_GROUPS.values() for t in types})


def rule_group(product_type: str | None) -> str | None:
    for group, types in RULE_GROUPS.items():
        if product_type in types:
            return group
    return None
