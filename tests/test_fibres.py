"""Fibre compositions: EU fibre names, percentages, order."""

import re

import pytest

from listingaudit.checks.fibres import (
    Fibre,
    check_fibre_composition,
    is_eu_fibre_name,
    parse_composition,
)
from listingaudit.rulebook.build import load_articles
from listingaudit.rulebook.download import raw_file
from listingaudit.rulebook.sources import SOURCES_BY_ID


def codes(make_offer, composition: str | None) -> list[str]:
    result = check_fibre_composition(make_offer(fibre_composition=composition))
    return [finding.code for finding in result.findings]


@pytest.mark.parametrize(
    "composition",
    [
        "100% cotton",
        "60% cotton, 40% polyester",
        "Cotton 60%, Polyester 40%",
        "60% cotton / 40% linen",
        "62.5% cotton, 37.5% viscose",
        "70% alpaca wool, 30% silk",
        "95% wool, 5% other fibres",
        "100% nylon",
        "mixed fibres",
        "front cover: 52% polyester, 41% acrylic, 7% cotton; back cover: 100% polyester; "
        "stuffing: 100% polyester",
    ],
)
def test_good_compositions_pass(make_offer, composition):
    assert codes(make_offer, composition) == []


@pytest.mark.parametrize(
    "composition, expected",
    [
        (None, ["fibre_composition_missing"]),
        ("N/A", ["fibre_composition_missing"]),
        ("cotton feel", ["not_an_eu_fibre_name", "percentage_missing"]),
        ("premium fabric blend", ["not_an_eu_fibre_name", "percentage_missing"]),
        ("60% cotton, 40% rayon", ["not_an_eu_fibre_name"]),
        ("100% microfiber", ["not_an_eu_fibre_name"]),
        ("100% hemp", ["not_an_eu_fibre_name"]),
        ("cotton, polyester", ["percentage_missing"]),
        ("60% cotton, 30% polyester", ["percentages_dont_add_up"]),
        ("40% polyester, 60% cotton", ["not_largest_first"]),
        ("cover: 60% cotton, 30% linen; filling: 100% polyester", ["percentages_dont_add_up"]),
    ],
)
def test_bad_compositions_are_caught(make_offer, composition, expected):
    assert codes(make_offer, composition) == expected


def test_the_eu_name_is_suggested_for_a_common_other_name(make_offer):
    result = check_fibre_composition(make_offer(fibre_composition="60% cotton, 40% rayon"))
    assert "'viscose' for 'rayon'" in result.summary


def test_a_composition_is_split_into_parts_and_fibres():
    parts = parse_composition("Cover: 60% Cotton, 40% Viscose; filling: 100% polyester")
    assert [label for label, _ in parts] == ["cover", "filling"]
    assert parts[0][1] == [Fibre("cotton", 60), Fibre("viscose", 40)]


@pytest.mark.parametrize(
    "name, allowed",
    [
        ("linen", True),
        ("flax", True),
        ("cashmere wool", True),
        ("camel hair", True),
        ("horsehair", True),
        ("metallic yarn", True),
        ("glass fibre", True),
        ("rayon", False),
        ("bamboo", False),
        ("cotton feel", False),
    ],
)
def test_eu_fibre_names(name, allowed):
    assert is_eu_fibre_name(name) is allowed


def test_every_name_in_annex_i_is_accepted():
    source = SOURCES_BY_ID["textile"]
    if not raw_file(source).exists():
        pytest.skip("the law texts are not downloaded")
    annex = next(a for a in load_articles(source) if a.number == "Annex I")
    # Rows look like "30 | polyamide or nylon | fibre formed from ..."; "modal" sits on
    # its own lines
    rows = re.findall(r"(?m)^(\d+)\s*(?:\||\n)\s*([^|\n]+?)\s*(?:\||\n)", annex.text)
    names = {int(number): name.lower() for number, name in rows}
    assert sorted(names) == list(range(1, 49))
    for number, name in names.items():
        if number in (3, 48):  # described rather than named: "animal or horsehair, ..."
            continue
        if number == 2:  # "alpaca, llama, ..., otter, followed or not by the word 'wool'"
            candidates = name.split(", followed")[0].split(", ")
        else:
            name = name.replace("(or ", "or ")  # "flax (or linen)": both names
            name = re.sub(r"\s*\(.*$", "", name)  # "abaca (Manila hemp)": an explanation
            candidates = name.rstrip(")").split(" or ")
        for candidate in candidates:
            assert is_eu_fibre_name(candidate), (number, candidate)
