import json
from pathlib import Path

import pytest

from listingaudit.data.abo import (
    first_localized,
    localized,
    parse_listing,
    pick_language,
    weight_in_grams,
)
from listingaudit.data.categories import FOCUS_PRODUCT_TYPES, rule_group

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "listings.json").read_text())
RUG, SHOE, FOOD = FIXTURES


def test_english_is_preferred_over_german():
    assert pick_language(RUG) == "en_US"


def test_falls_back_to_the_only_language_available():
    assert pick_language(SHOE) == "fr_FR"


def test_no_language_when_title_has_no_tags():
    assert pick_language({"item_name": [{"value": "x"}]}) is None


def test_localized_keeps_only_the_chosen_language():
    assert localized(RUG["bullet_point"], "en_US") == ["Soft short pile", "Easy to clean"]


def test_untagged_values_count_for_every_language():
    assert first_localized(RUG["model_number"], "en_US") == "R-100"


@pytest.mark.parametrize(
    ("unit", "value", "grams"),
    [
        ("grams", 250, 250.0),
        ("kilograms", 1.5, 1500.0),
        ("pounds", 1, 453.592),
        ("ounces", 10, 283.495),
    ],
)
def test_weights_are_converted_to_grams(unit, value, grams):
    record = {"item_weight": [{"unit": unit, "value": value}]}
    assert weight_in_grams(record) == pytest.approx(grams, abs=0.001)


def test_unknown_weight_unit_gives_none():
    assert weight_in_grams(FOOD) is None


def test_missing_weight_gives_none():
    assert weight_in_grams(SHOE) is None


def test_parse_listing_flattens_a_record():
    row = parse_listing(RUG)
    assert row["item_id"] == "B0TESTRUG1"
    assert row["product_type"] == "RUG"
    assert row["item_name"] == "Short Pile Rug, Blue, 120 x 170 cm"
    assert row["color"] == "Blue"
    assert row["fabric_type"] == "100% Polypropylene"
    assert row["weight_grams"] == pytest.approx(907.185)
    assert row["other_image_ids"] == ["91testB", "91testC"]
    assert row["category_path"] == "/Home/Rugs"


def test_brand_falls_back_to_any_language():
    # The rug's brand is only tagged de_DE, but its text language is en_US
    assert parse_listing(RUG)["brand"] == "Rivet"


def test_missing_optional_fields_become_empty():
    row = parse_listing(FOOD)
    assert row["color"] is None
    assert row["bullet_points"] == []
    assert row["other_image_ids"] == []
    assert row["main_image_id"] is None


def test_rule_groups():
    assert rule_group("RUG") == "textile"
    assert rule_group("GROCERY") == "unit_price"
    assert rule_group("CELLULAR_PHONE_CASE") is None
    assert "LAMP" in FOCUS_PRODUCT_TYPES
