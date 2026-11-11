"""Turn raw ABO listing records into flat rows for the database.

A raw ABO record stores most text fields as a list of translations:

    "color": [{"language_tag": "de_DE", "value": "Blau"},
              {"language_tag": "en_US", "value": "Blue"}]

Each listing is read in a single language, so a row never mixes German and
English text.
"""

from typing import Any

# English first, then German (the target market)
LANGUAGE_PREFERENCE = (
    "en_GB",
    "en_US",
    "en_IN",
    "en_CA",
    "en_AU",
    "en_SG",
    "en_AE",
    "de_DE",
)

# ABO weights come in mixed units; everything is stored in grams
GRAMS_PER_UNIT = {
    "milligrams": 0.001,
    "grams": 1.0,
    "kilograms": 1000.0,
    "ounces": 28.349523125,
    "pounds": 453.59237,
}

Record = dict[str, Any]


def pick_language(record: Record) -> str | None:
    """Choose the language for a listing, based on the languages its title exists in."""
    tags = [v.get("language_tag") for v in record.get("item_name", []) if v.get("language_tag")]
    for preferred in LANGUAGE_PREFERENCE:
        if preferred in tags:
            return preferred
    return tags[0] if tags else None


def localized(values: list[dict], language: str | None) -> list[str]:
    """All values of a translated field in the given language.

    Values without a language tag (model numbers, years) count for every language.
    """
    return [
        str(v["value"])
        for v in values
        if v.get("language_tag") in (language, None) and v.get("value") not in (None, "")
    ]


def first_localized(values: list[dict], language: str | None) -> str | None:
    matches = localized(values, language)
    return matches[0] if matches else None


def weight_in_grams(record: Record) -> float | None:
    weights = record.get("item_weight") or []
    if not weights:
        return None
    unit = weights[0].get("unit")
    value = weights[0].get("value")
    if unit not in GRAMS_PER_UNIT or value is None:
        return None
    return round(float(value) * GRAMS_PER_UNIT[unit], 3)


def parse_listing(record: Record) -> dict[str, Any]:
    """Flatten one raw ABO record into the columns of the `listings` table."""
    lang = pick_language(record)
    product_types = record.get("product_type") or []
    nodes = record.get("node") or []

    # Brand names aren't translated, so fall back to a brand in any language
    brands = record.get("brand") or []
    brand = first_localized(brands, lang) or (str(brands[0]["value"]) if brands else None)

    return {
        "item_id": record["item_id"],
        "domain_name": record["domain_name"],
        "marketplace": record.get("marketplace"),
        "country": record.get("country"),
        "product_type": product_types[0]["value"] if product_types else None,
        "text_language": lang,
        "item_name": first_localized(record.get("item_name", []), lang),
        "brand": brand,
        "color": first_localized(record.get("color", []), lang),
        "material": first_localized(record.get("material", []), lang),
        "fabric_type": first_localized(record.get("fabric_type", []), lang),
        "bullet_points": localized(record.get("bullet_point", []), lang),
        "description": first_localized(record.get("product_description", []), lang),
        "weight_grams": weight_in_grams(record),
        "main_image_id": record.get("main_image_id"),
        "other_image_ids": record.get("other_image_id", []),
        "category_path": nodes[0].get("node_name") if nodes else None,
    }
