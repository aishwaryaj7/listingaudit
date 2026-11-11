"""Load the downloaded ABO files into Postgres.

    uv run python -m listingaudit.data.load_abo

Re-running drops and reloads both tables.
"""

import csv
import gzip
import json
from collections.abc import Iterator

from psycopg.types.json import Jsonb
from tqdm import tqdm

from listingaudit.config import settings
from listingaudit.data.abo import parse_listing
from listingaudit.data.download_abo import IMAGE_INDEX
from listingaudit.db import apply_schema, connect

LISTING_COLUMNS = [
    "item_id",
    "domain_name",
    "marketplace",
    "country",
    "product_type",
    "text_language",
    "item_name",
    "brand",
    "color",
    "material",
    "fabric_type",
    "bullet_points",
    "description",
    "weight_grams",
    "main_image_id",
    "other_image_ids",
    "category_path",
]


def read_raw_listings() -> Iterator[dict]:
    files = sorted(settings.abo_raw_dir.glob("listings_*.json.gz"))
    if not files:
        raise SystemExit("No listing files found. Run listingaudit.data.download_abo first.")
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield json.loads(line)


def load_images(cur) -> int:
    path = settings.abo_raw_dir / IMAGE_INDEX.split("/")[-1]
    count = 0
    with (
        gzip.open(path, "rt", encoding="utf-8") as f,
        cur.copy("COPY images (image_id, height, width, path) FROM STDIN") as copy,
    ):
        for row in csv.DictReader(f):
            copy.write_row((row["image_id"], int(row["height"]), int(row["width"]), row["path"]))
            count += 1
    return count


def load_listings(cur) -> int:
    columns = ", ".join(LISTING_COLUMNS + ["raw"])
    count = 0
    with cur.copy(f"COPY listings ({columns}) FROM STDIN") as copy:
        for record in tqdm(read_raw_listings(), desc="listings", unit=" rows"):
            row = parse_listing(record)
            copy.write_row([row[c] for c in LISTING_COLUMNS] + [Jsonb(record)])
            count += 1
    return count


# Some photos are shared by many products (brand logos, "image coming soon"
# placeholders, one photo for every size and colour). download_images uses
# these counts to skip them.
COUNT_PHOTO_USE = """
UPDATE images i
SET used_by_items = s.items, used_by_types = s.types
FROM (
    SELECT main_image_id,
           count(DISTINCT item_id) AS items,
           count(DISTINCT product_type) AS types
    FROM listings
    WHERE main_image_id IS NOT NULL
    GROUP BY main_image_id
) s
WHERE i.image_id = s.main_image_id
"""


def main() -> None:
    # One transaction: if anything fails, the database keeps its old data.
    with connect() as conn, conn.cursor() as cur:
        # Both tables are derived from the downloaded files, so recreate them
        # (this also picks up schema changes)
        cur.execute("DROP TABLE IF EXISTS listings, images")
        apply_schema(conn)
        n_images = load_images(cur)
        n_listings = load_listings(cur)
        cur.execute(COUNT_PHOTO_USE)
    print(f"Loaded {n_images:,} images and {n_listings:,} listings.")


if __name__ == "__main__":
    main()
