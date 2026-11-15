"""Colour names from listings, mapped to colour families."""

import pytest

from listingaudit.photos.colours import FAMILIES, NEAR, WORDS, family_of, is_near, noun


@pytest.mark.parametrize(
    "colour, family",
    [
        ("Black", "black"),
        ("NAVY", "blue"),
        ("Polished Nickel", "grey"),
        ("Antique Brass", "gold"),
        ("Oil-rubbed Bronze", "brown"),
        ("Soft Cream", "white"),
        ("Dark Tan", "beige"),
        ("L.BLUE", "blue"),
        ("Dusty Pink", "pink"),
        ("Ink", "blue"),
    ],
)
def test_shop_colour_names_map_to_a_family(colour, family):
    assert family_of(colour) == family


@pytest.mark.parametrize(
    "colour, family",
    [("Pearl Gray", "grey"), ("Slate Blue", "blue"), ("Seafoam Green", "green")],
)
def test_the_last_colour_word_decides(colour, family):
    assert family_of(colour) == family


def test_a_fabric_word_does_not_change_the_colour():
    assert family_of("Grey Linen") == "grey"


def test_two_parts_in_one_family_still_count_as_one_colour():
    assert family_of("Blush/Coral") == "pink"


@pytest.mark.parametrize(
    "colour",
    ["Walnut/White", "Brass and White", "Black With Wood", "Beige, Brown, Orange"],
)
def test_names_of_several_colours_have_no_family(colour):
    assert family_of(colour) is None


@pytest.mark.parametrize("colour", [None, "", "multi", "Multi Color", "Clear", "Fiesta", "Others"])
def test_unknown_and_mixed_colours_have_no_family(colour):
    assert family_of(colour) is None


def test_every_word_belongs_to_one_family():
    words = [word for family in WORDS.values() for word in family.split()]
    assert len(words) == len(set(words))
    assert set(WORDS) == set(FAMILIES)


def test_near_colours_are_pairs_of_known_families():
    assert all(len(pair) == 2 and pair <= set(FAMILIES) for pair in NEAR)
    assert is_near("beige", "white") and is_near("white", "beige")
    assert not is_near("red", "blue")


def test_product_types_become_nouns():
    assert noun("FLAT_SHEET") == "bed sheet"
    assert noun("SHOES") == "pair of shoes"
    assert noun("GROCERY") == "product"
