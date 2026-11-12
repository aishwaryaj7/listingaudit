"""Unit prices: reading pack sizes and working out the price per kg or litre."""

from decimal import Decimal

import pytest

from listingaudit.checks.unit_price import Quantity, check_unit_price, parse_quantity


@pytest.mark.parametrize(
    "text, amount, unit",
    [
        ("500 g", "0.5", "kg"),
        ("3 x 250 ml", "0.75", "l"),
        ("2×1kg", "2", "kg"),
        ("1,5 l", "1.5", "l"),
        ("75 cl", "0.75", "l"),
        ("12 x 113 g", "1.356", "kg"),
    ],
)
def test_pack_sizes_are_read_in_kg_or_litres(text, amount, unit):
    assert parse_quantity(text) == Quantity(Decimal(amount), unit)


@pytest.mark.parametrize("text", ["about 500 g", "500", "16 oz", "1 pack"])
def test_unclear_or_non_metric_sizes_are_not_guessed(text):
    assert parse_quantity(text) is None


@pytest.fixture
def packed(make_offer):
    def make(price: str, quantity: str | None, unit_price: str | None = None, per=None):
        return make_offer(
            product_type="COFFEE",
            price=price,
            net_quantity=quantity,
            unit_price=unit_price,
            unit_price_per=per,
        )

    return make


def codes(result) -> list[str]:
    return [finding.code for finding in result.findings]


def test_a_right_unit_price_passes(packed):
    assert check_unit_price(packed("4.49", "340 g", "13.21", "kg")).status == "ok"


def test_a_missing_unit_price_is_caught(packed):
    result = check_unit_price(packed("4.49", "340 g"))
    assert codes(result) == ["unit_price_missing"]
    assert "€13.21 per kg" in result.summary


def test_a_unit_price_per_100_g_is_the_wrong_unit_for_packed_goods(packed):
    result = check_unit_price(packed("4.49", "340 g", "1.32", "100 g"))
    assert codes(result) == ["unit_price_wrong_unit"]


def test_a_unit_price_worked_out_from_one_item_of_a_multipack_is_caught(packed):
    # €4.49 for 12 x 40 g is €9.35 per kg; €112.25 is the price per kg of one bag
    result = check_unit_price(packed("4.49", "12 x 40 g", "112.25", "kg"))
    assert codes(result) == ["unit_price_wrong"]


def test_a_unit_price_for_volume_on_goods_sold_by_weight_is_caught(packed):
    assert codes(check_unit_price(packed("4.49", "340 g", "13.21", "l"))) == [
        "unit_price_wrong_unit"
    ]


@pytest.mark.parametrize("shown, status", [("3.33", "ok"), ("3.34", "ok"), ("3.35", "problem")])
def test_the_last_cent_may_be_rounded_either_way(packed, shown, status):
    # €1.00 for 300 g is €3.333... per kg
    assert check_unit_price(packed("1.00", "300 g", shown, "kg")).status == status


def test_a_1_kg_pack_may_leave_out_the_unit_price(packed):
    assert check_unit_price(packed("9.49", "1000 g")).status == "ok"
    assert check_unit_price(packed("9.49", "1 kg", "9.49", "kg")).status == "ok"


def test_less_than_10_g_needs_no_unit_price(packed):
    assert check_unit_price(packed("2.99", "8 g")).status == "not_applicable"


@pytest.mark.parametrize("quantity", [None, "n/a", "one jar"])
def test_without_a_readable_pack_size_the_unit_price_cannot_be_checked(packed, quantity):
    assert check_unit_price(packed("4.49", quantity)).status == "cannot_check"
