"""Pick CLIP's thresholds on spare photos: downloaded photos that no practice or exam
listing shows, so the practice score stays a fair test of the chosen values.

Each spare photo is paired with its own colour, which it should fit, and with every
other colour that spare photos of the same kind of product have: a made-up swap.

    uv run python -m listingaudit.photos.tune
"""

from collections import defaultdict
from dataclasses import dataclass

import pandas as pd
from psycopg.rows import namedtuple_row

from listingaudit.config import settings
from listingaudit.db import connect
from listingaudit.photos.check import MATCH_MIN, MISMATCH_MAX, judge
from listingaudit.photos.clip import Clip, get_clip
from listingaudit.photos.colours import NOUNS, family_of, is_near, noun

# Main photos of products none of whose photos appear in any listing, swapped in or not
SPARE = """
SELECT DISTINCT ON (i.image_id) l.product_type, l.color, i.local_file
FROM listings l
JOIN images i ON i.image_id = l.main_image_id
WHERE i.local_file IS NOT NULL
  AND l.item_id NOT IN (
      SELECT s.item_id FROM listings s JOIN market.offers o ON o.image_id = s.main_image_id
  )
ORDER BY i.image_id
"""

MATCH_GRID = [0.05, 0.10, 0.15, 0.20, 0.30]
MISMATCH_GRID = [0.005, 0.01, 0.02, 0.05]
KINDS = ["fits", "easy swap", "hard swap"]


@dataclass
class SparePhoto:
    product_type: str
    colour: str
    family: str
    image_file: str


def spare_photos(conn) -> list[SparePhoto]:
    """Spare photos of the kinds of product the check knows, with one clear colour."""
    with conn.cursor(row_factory=namedtuple_row) as cur:
        rows = cur.execute(SPARE).fetchall()
    return [
        SparePhoto(row.product_type, row.color, family, row.local_file)
        for row in rows
        if row.product_type in NOUNS and (family := family_of(row.color))
    ]


def score_photos(photos: list[SparePhoto], clip: Clip) -> list[dict[str, float]]:
    vectors = clip.encode_images([settings.data_dir / photo.image_file for photo in photos])
    return [
        clip.colour_scores(vector, noun(photo.product_type))
        for photo, vector in zip(photos, vectors, strict=True)
    ]


def make_pairs(photos: list[SparePhoto]) -> pd.DataFrame:
    """One row per photo and listing colour: the photo's own colour, or a made-up swap.

    A swap to a near colour (cream for white) is a hard one, as in the answer key.
    """
    colours = defaultdict(set)
    for photo in photos:
        colours[photo.product_type].add(photo.family)
    rows = []
    for index, photo in enumerate(photos):
        for colour in sorted(colours[photo.product_type]):
            if colour == photo.family:
                kind = "fits"
            else:
                kind = "hard swap" if is_near(colour, photo.family) else "easy swap"
            rows.append({"photo": index, "listing_colour": colour, "kind": kind})
    return pd.DataFrame(rows)


def rates(
    pairs: pd.DataFrame, scores: list[dict[str, float]], match_min: float, mismatch_max: float
) -> dict[str, float]:
    """For one pair of thresholds: how often fitting photos get flagged or passed on,
    and how many made-up swaps get caught or let through."""
    outcomes = pd.Series(
        [
            judge(scores[photo], colour, match_min, mismatch_max).outcome
            for photo, colour in zip(pairs["photo"], pairs["listing_colour"], strict=True)
        ],
        name="outcome",
    )
    share = pd.crosstab(pairs["kind"], outcomes, normalize="index").reindex(
        index=KINDS, columns=["match", "unclear", "mismatch"], fill_value=0
    )
    return {
        "fits: flagged": share.loc["fits", "mismatch"],
        "fits: unclear": share.loc["fits", "unclear"],
        "easy swap: caught": share.loc["easy swap", "mismatch"],
        "easy swap: passed": share.loc["easy swap", "match"],
        "hard swap: caught": share.loc["hard swap", "mismatch"],
        "hard swap: passed": share.loc["hard swap", "match"],
    }


def grid(pairs: pd.DataFrame, scores: list[dict[str, float]]) -> pd.DataFrame:
    rows = [
        {"match_min": match_min, "mismatch_max": mismatch_max}
        | rates(pairs, scores, match_min, mismatch_max)
        for match_min in MATCH_GRID
        for mismatch_max in MISMATCH_GRID
    ]
    return pd.DataFrame(rows).set_index(["match_min", "mismatch_max"])


def main() -> None:
    with connect() as conn:
        photos = spare_photos(conn)
    scores = score_photos(photos, get_clip())
    pairs = make_pairs(photos)
    right = [max(s, key=s.get) == photo.family for photo, s in zip(photos, scores, strict=True)]
    accuracy = sum(right) / len(right)
    print(f"{len(photos)} spare photos; CLIP names the colour family right for {accuracy:.0%}.")
    print(pairs["kind"].value_counts().reindex(KINDS).to_string(), "\n")
    table = grid(pairs, scores)
    with pd.option_context("display.width", 200):
        print((table * 100).round(1).to_string())
    print(f"\nIn use: MATCH_MIN = {MATCH_MIN}, MISMATCH_MAX = {MISMATCH_MAX}")


if __name__ == "__main__":
    main()
