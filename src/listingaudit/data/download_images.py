"""Download main photos for a sample of the focus product types.

    uv run python -m listingaudit.data.download_images --per-type 100

Takes up to N products per type in categories.FOCUS_PRODUCT_TYPES, since the
full set of 398k ABO photos is many gigabytes. The sample is deterministic for
a given seed. Products whose main photo is shared with other products are
skipped, as shared photos are mostly logos and placeholders.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor

import httpx
from tqdm import tqdm

from listingaudit.config import settings
from listingaudit.data.categories import FOCUS_PRODUCT_TYPES
from listingaudit.db import connect

SAMPLE_QUERY = """
WITH products AS (
    -- the same product can be listed on several Amazon sites: keep one listing
    SELECT DISTINCT ON (l.item_id) l.item_id, l.product_type, i.image_id, i.path
    FROM listings l
    JOIN images i ON i.image_id = l.main_image_id
    WHERE l.product_type = ANY(%(types)s)
      AND l.text_language LIKE 'en%%'
      AND i.used_by_items = 1
    ORDER BY l.item_id, l.domain_name
),
ranked AS (
    SELECT image_id, path,
           row_number() OVER (
               PARTITION BY product_type ORDER BY md5(item_id || %(seed)s)
           ) AS n
    FROM products
)
SELECT image_id, path FROM ranked WHERE n <= %(per_type)s
"""


def fetch(client: httpx.Client, image_id: str, path: str, size: str) -> tuple[str, str]:
    target = settings.images_dir / size / path
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        response = client.get(f"{settings.abo_base_url}/images/{size}/{path}")
        response.raise_for_status()
        target.write_bytes(response.content)
    return image_id, str(target.relative_to(settings.data_dir))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-type", type=int, default=100, help="listings per product type")
    parser.add_argument(
        "--size",
        choices=["small", "original"],
        default="small",
        help="'small' photos are at most 256 px and only a few KB each",
    )
    parser.add_argument("--seed", default="listingaudit")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    with connect() as conn:
        rows = conn.execute(
            SAMPLE_QUERY,
            {"types": FOCUS_PRODUCT_TYPES, "per_type": args.per_type, "seed": args.seed},
        ).fetchall()

        with httpx.Client(timeout=30) as client, ThreadPoolExecutor(args.workers) as pool:
            jobs = [pool.submit(fetch, client, img, path, args.size) for img, path in rows]
            done = [job.result() for job in tqdm(jobs, desc="photos")]

        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE images SET local_file = %s WHERE image_id = %s",
                [(local_file, image_id) for image_id, local_file in done],
            )

    print(f"{len(done):,} photos ready in {settings.images_dir / args.size}")


if __name__ == "__main__":
    main()
