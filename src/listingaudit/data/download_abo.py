"""Download the ABO listing metadata and the image index.

    uv run python -m listingaudit.data.download_abo

About 96 MB. Complete files are skipped, so it can be rerun after a failed
download.
"""

from pathlib import Path

import httpx
from tqdm import tqdm

from listingaudit.config import settings

LISTING_FILES = [f"listings/metadata/listings_{c}.json.gz" for c in "0123456789abcdef"]
IMAGE_INDEX = "images/metadata/images.csv.gz"


def download(client: httpx.Client, url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)

    expected_size = int(client.head(url).headers.get("content-length", 0))
    if target.exists() and target.stat().st_size == expected_size:
        return

    # Write to a .part file first so an interrupted download never looks complete
    partial = target.with_suffix(target.suffix + ".part")
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with partial.open("wb") as f:
            for chunk in response.iter_bytes(chunk_size=1 << 16):
                f.write(chunk)
    partial.replace(target)


def main() -> None:
    files = LISTING_FILES + [IMAGE_INDEX]
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for path in tqdm(files, desc="ABO metadata"):
            target = settings.abo_raw_dir / Path(path).name
            download(client, f"{settings.abo_base_url}/{path}", target)
    print(f"Done. Files are in {settings.abo_raw_dir}")


if __name__ == "__main__":
    main()
