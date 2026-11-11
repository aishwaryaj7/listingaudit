"""Download the law texts for the rulebook.

    uv run python -m listingaudit.rulebook.download

Nine files, about 3 MB. Existing files are skipped; delete one to fetch it again
(e.g. after the law changes).
"""

from pathlib import Path

import httpx

from listingaudit.config import settings
from listingaudit.rulebook.sources import SOURCES, LawSource


def raw_file(source: LawSource) -> Path:
    return settings.laws_dir / "raw" / source.file_name


# Markup the parser relies on, which error and "please wait" pages lack
PAGE_MARKERS = {
    "act": b'class="eli-subdivision"',  # each article of a law
    "guidance": b'class="oj-ti-grseq-1"',  # each section heading of a notice
    "judgment": b'class="coj-count"',  # each paragraph number of a judgment
}


def looks_complete(source: LawSource, content: bytes) -> bool:
    """Quick check that the download is the law text, not an error page."""
    if source.format == "gii_xml":
        return content.startswith(b"PK")  # every zip file starts with these two bytes
    return PAGE_MARKERS[source.kind] in content


def download(client: httpx.Client, source: LawSource) -> None:
    target = raw_file(source)
    if target.exists():
        return
    response = client.get(source.url)
    response.raise_for_status()
    if not looks_complete(source, response.content):
        raise RuntimeError(
            f"{source.url} did not return the law text. Open it in a browser to check."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)


def main() -> None:
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for source in SOURCES:
            download(client, source)
            print(f"{source.file_name:20} {source.name}")
    print(f"Done. Files are in {settings.laws_dir / 'raw'}")


if __name__ == "__main__":
    main()
