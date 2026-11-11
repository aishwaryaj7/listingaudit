"""The rules checklist (checklist.yaml) is well formed and agrees with the marketplace."""

from typing import get_args

import pytest
from pydantic import ValidationError

from listingaudit.market.catalogue import Kind
from listingaudit.market.models import Offer
from listingaudit.market.rules import Rule, applicable_rules
from listingaudit.rulebook.checklist import Checklist, load_checklist
from listingaudit.rulebook.sources import SOURCES_BY_ID


@pytest.fixture(scope="module")
def checklist() -> Checklist:
    return load_checklist()


def test_every_rule_has_checks_and_a_law(checklist):
    assert set(checklist.rules) == set(Rule)
    for spec in checklist.rules.values():
        assert spec.checks and spec.law


def test_every_product_kind_is_covered(checklist):
    assert set(checklist.product_kinds) == set(get_args(Kind))


def test_evidence_fields_exist_on_a_listing(checklist):
    known = set(Offer.model_fields) | {"price_history"}
    for rule, spec in checklist.rules.items():
        assert set(spec.fields) <= known, rule


def test_law_references_name_known_sources(checklist):
    for spec in checklist.rules.values():
        for ref in spec.law:
            assert ref.law in SOURCES_BY_ID


@pytest.mark.parametrize("announces_reduction", [True, False])
@pytest.mark.parametrize("maker_in_eu", [True, False])
def test_same_rules_as_the_marketplace(made_up, checklist, announces_reduction, maker_in_eu):
    """The agent's checklist and the answer key must agree on which rules apply."""
    products, _ = made_up
    for product in products:
        expected = applicable_rules(product, announces_reduction, maker_in_eu)
        got = checklist.rules_for(
            product.kind, product.product_type, announces_reduction, maker_in_eu
        )
        assert got == expected, (product.kind, product.product_type)


def test_examples(checklist):
    rug = checklist.rules_for("textile", "RUG", announces_reduction=True, maker_in_eu=False)
    assert rug == [
        Rule.PRIOR_PRICE,
        Rule.MANUFACTURER_INFO,
        Rule.EU_RESPONSIBLE_PERSON,
        Rule.FIBRE_COMPOSITION,
        Rule.PHOTO_MATCHES_TEXT,
    ]
    coffee = checklist.rules_for(
        "unit_price", "COFFEE", announces_reduction=False, maker_in_eu=False
    )
    assert coffee == [Rule.UNIT_PRICE, Rule.PHOTO_MATCHES_TEXT]  # food: no GPSR rules


def test_a_typo_in_the_file_is_an_error(checklist):
    data = checklist.model_dump(mode="json")
    data["rules"]["prior_price"]["chekcs"] = data["rules"]["prior_price"].pop("checks")
    with pytest.raises(ValidationError):
        Checklist.model_validate(data)


def test_an_unknown_product_kind_is_an_error(checklist):
    with pytest.raises(ValueError, match="unknown product kind"):
        checklist.rules_for("spaceship", "TOY", announces_reduction=False, maker_in_eu=True)
