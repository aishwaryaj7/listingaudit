"""Picking CLIP's thresholds on spare photos, with made-up photos and scores."""

import psycopg
import pytest

from listingaudit.db import connect
from listingaudit.photos.colours import FAMILIES
from listingaudit.photos.tune import (
    MATCH_GRID,
    MISMATCH_GRID,
    SparePhoto,
    grid,
    make_pairs,
    rates,
    spare_photos,
)


def photo(product_type: str, family: str) -> SparePhoto:
    return SparePhoto(product_type, family.title(), family, f"images/{family}.jpg")


def spread(**given: float) -> dict[str, float]:
    rest = (1 - sum(given.values())) / (len(FAMILIES) - len(given))
    return {family: given.get(family, rest) for family in FAMILIES}


def test_each_photo_is_paired_with_every_colour_of_its_kind_of_product():
    photos = [photo("LAMP", "white"), photo("LAMP", "beige"), photo("LAMP", "red")]
    photos.append(photo("SOFA", "blue"))
    pairs = make_pairs(photos)
    white_lamp = pairs[pairs["photo"] == 0].set_index("listing_colour")["kind"].to_dict()
    assert white_lamp == {"beige": "hard swap", "red": "easy swap", "white": "fits"}
    assert pairs[pairs["photo"] == 3]["kind"].tolist() == ["fits"]


def test_rates_count_flagged_photos_and_caught_swaps():
    photos = [photo("LAMP", "white"), photo("LAMP", "red")]
    scores = [spread(white=0.9), spread(red=0.99)]
    pairs = make_pairs(photos)
    assert rates(pairs, scores, match_min=0.15, mismatch_max=0.01) == {
        "fits: flagged": 0,
        "fits: unclear": 0,
        "easy swap: caught": 1,
        "easy swap: passed": 0,
        "hard swap: caught": 0,
        "hard swap: passed": 0,
    }
    # The white photo leaves red just under 1%: a lower limit no longer flags it
    assert rates(pairs, scores, match_min=0.15, mismatch_max=0.005)["easy swap: caught"] == 0.5


def test_the_grid_has_a_row_for_each_pair_of_thresholds():
    photos = [photo("LAMP", "white"), photo("LAMP", "red")]
    table = grid(make_pairs(photos), [spread(white=0.9), spread(red=0.99)])
    assert len(table) == len(MATCH_GRID) * len(MISMATCH_GRID)


def test_no_listing_shows_a_spare_photo():
    try:
        conn = connect()
    except psycopg.OperationalError:
        pytest.skip("the database is not running")
    with conn:
        if conn.execute("SELECT to_regclass('market.offers')").fetchone()[0] is None:
            pytest.skip("no marketplace yet: run python -m listingaudit.market.build")
        photos = spare_photos(conn)
        shown = {row[0] for row in conn.execute("SELECT image_file FROM market.offers")}
    assert len(photos) > 100
    assert not {p.image_file for p in photos} & shown
