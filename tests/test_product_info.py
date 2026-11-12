"""Manufacturer, responsible person, safety warnings and energy label."""

import pytest

from listingaudit.checks.product_info import (
    EU_COUNTRIES,
    check_energy_label,
    check_eu_responsible_person,
    check_manufacturer_info,
    check_safety_warnings,
    in_eu,
)
from listingaudit.market.companies import EU, NON_EU


def codes(result) -> list[str]:
    return [finding.code for finding in result.findings]


# --- Manufacturer ------------------------------------------------------------------


def test_a_complete_manufacturer_passes(make_offer):
    assert check_manufacturer_info(make_offer()).status == "ok"


def test_a_website_counts_as_the_electronic_address(make_offer):
    offer = make_offer(manufacturer_email=None, manufacturer_website="https://testmaker.example")
    assert check_manufacturer_info(offer).status == "ok"


def test_a_missing_postal_address_is_caught(make_offer):
    offer = make_offer(manufacturer_address=None, manufacturer_website="https://testmaker.example")
    assert codes(check_manufacturer_info(offer)) == ["manufacturer_address_missing"]


def test_a_name_and_country_alone_are_not_enough(make_offer):
    offer = make_offer(manufacturer_address="N/A", manufacturer_email=None)
    assert codes(check_manufacturer_info(offer)) == [
        "manufacturer_address_missing",
        "manufacturer_contact_missing",
    ]


def test_food_is_outside_the_product_safety_rules(make_offer):
    offer = make_offer(product_type="COFFEE", manufacturer_address=None)
    assert check_manufacturer_info(offer).status == "not_applicable"
    assert check_eu_responsible_person(offer).status == "not_applicable"


# --- Responsible person in the EU ----------------------------------------------------


@pytest.fixture
def made_in(make_offer):
    def make(country: str, **changes):
        return make_offer(manufacturer_country=country, **changes)

    return make


RP = {
    "eu_rp_name": "Gemza Product Compliance Sp. z o.o.",
    "eu_rp_address": "aleja Głowackiego 235, 14-095 Kołobrzeg, Poland",
    "eu_rp_email": "gpsr@gemza.example",
}


def test_a_maker_outside_the_eu_with_a_responsible_person_passes(made_in):
    result = check_eu_responsible_person(made_in("China", **RP))
    assert result.status == "ok"
    assert "in Poland" in result.summary


@pytest.mark.parametrize("country", ["Germany", "Slovenia", "Croatia", "Malta"])
def test_a_maker_in_the_eu_needs_no_responsible_person(made_in, country):
    assert check_eu_responsible_person(made_in(country)).status == "not_applicable"


@pytest.mark.parametrize("country", ["China", "United Kingdom", "Switzerland", "Turkey"])
def test_a_maker_outside_the_eu_without_a_responsible_person_is_caught(made_in, country):
    result = check_eu_responsible_person(made_in(country))
    assert codes(result) == ["responsible_person_missing"]


def test_a_responsible_person_outside_the_eu_is_caught(made_in):
    offer = made_in("China", **RP | {"eu_rp_address": "Bahnhofstrasse 1, 8001 Zürich, Switzerland"})
    assert codes(check_eu_responsible_person(offer)) == ["responsible_person_outside_eu"]


def test_a_responsible_person_without_an_email_is_caught(made_in):
    offer = made_in("China", **RP | {"eu_rp_email": None})
    assert codes(check_eu_responsible_person(offer)) == ["responsible_person_email_missing"]


def test_the_makers_country_can_come_from_its_address(made_in):
    offer = made_in(None, manufacturer_address="No. 65 Xinhu Road, Guangzhou 510000, China")
    assert codes(check_eu_responsible_person(offer)) == ["responsible_person_missing"]


def test_without_the_makers_country_the_rule_cannot_be_checked(made_in):
    offer = made_in(None, manufacturer_address=None)
    assert check_eu_responsible_person(offer).status == "cannot_check"


def test_the_eu_has_27_members():
    assert len(EU_COUNTRIES - {"czech republic", "the netherlands"}) == 27


def test_the_marketplace_countries_are_sorted_correctly():
    assert all(in_eu(name) for name, _ in EU.values())
    assert not any(in_eu(name) for name, _ in NON_EU.values())


# --- Safety warnings and energy label ------------------------------------------------


def test_missing_warnings_are_caught(make_offer):
    result = check_safety_warnings(make_offer(product_type="CANDLE"))
    assert codes(result) == ["safety_warnings_missing"]


def test_warnings_are_found_but_not_judged(make_offer):
    # The wrong warnings for a candle: code can only see that some are there
    offer = make_offer(product_type="CANDLE", safety_warnings="Store in a cool, dry place.")
    assert check_safety_warnings(offer).status == "ok"


LABEL = {
    "energy_class": "F",
    "energy_label_url": "https://eprel.example/label/123456.svg",
    "product_sheet_url": "https://eprel.example/sheet/123456",
}


def test_a_complete_energy_label_passes(make_offer):
    assert check_energy_label(make_offer(product_type="LIGHT_BULB", **LABEL)).status == "ok"


@pytest.mark.parametrize(
    "left_out, expected",
    [
        (["product_sheet_url"], ["product_sheet_missing"]),
        (
            ["energy_label_url", "product_sheet_url"],
            ["energy_label_missing", "product_sheet_missing"],
        ),
        (list(LABEL), ["energy_class_missing", "energy_label_missing", "product_sheet_missing"]),
    ],
)
def test_missing_label_information_is_caught(make_offer, left_out, expected):
    fields = {key: value for key, value in LABEL.items() if key not in left_out}
    assert codes(check_energy_label(make_offer(product_type="LIGHT_BULB", **fields))) == expected
