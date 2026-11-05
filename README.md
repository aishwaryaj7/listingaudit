# ListingAudit

ListingAudit is an AI agent that audits online shop listings against EU consumer and
product-safety rules: honest discounts (the 30-day price rule), manufacturer and safety
information (GPSR), unit prices, textile fibre labels, energy labels, and photos that
match the description. It writes an audit report with evidence, and a human reviewer
approves every finding.

> Status: **step 1 of 10**, data and project setup. The agent itself comes in later steps.

## What's here so far

- A Postgres database (in Docker) holding the full
  [Amazon Berkeley Objects](https://amazon-berkeley-objects.s3.amazonaws.com/index.html)
  catalogue: 147,702 product listings and an index of 398,212 product photos.
- Scripts to download the data, clean it, and load it into the database.
- Product photos for a sample of the categories our rules care about
  (textiles, lamps, groceries, cosmetics, candles, furniture).
- A notebook exploring the data: `notebooks/01_explore_abo.ipynb`.
- Unit tests for the data cleaning.

## Quick start

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) and
[uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
# 1. Settings (the defaults work as they are)
cp .env.example .env

# 2. Install Python 3.12 and all packages into .venv
uv sync

# 3. Start the database
docker compose up -d

# 4. Download the ABO metadata (~96 MB) and load it into Postgres
uv run python -m listingaudit.data.download_abo
uv run python -m listingaudit.data.load_abo

# 5. Download photos for a sample of listings (~1,900 small photos, about 20 MB)
uv run python -m listingaudit.data.download_images --per-type 100

# 6. Run the tests
uv run pytest

# 7. Explore the data
uv run jupyter lab notebooks/01_explore_abo.ipynb
```

On Windows PowerShell, use `copy .env.example .env` in step 1. Everything else is the same.

## Project layout

```
db/schema.sql                    database tables
src/listingaudit/config.py          settings (read from .env)
src/listingaudit/db.py              database connection helpers
src/listingaudit/data/abo.py        cleans one raw ABO record into a flat row
src/listingaudit/data/categories.py which product types matter for which rule
src/listingaudit/data/download_*.py download scripts
src/listingaudit/data/load_abo.py   loads the data into Postgres
notebooks/                       data exploration
tests/                           unit tests
data/                            downloaded files (not in git)
```

## Data and licence

Product data and photos come from the Amazon Berkeley Objects (ABO) dataset by
Amazon.com and UC Berkeley, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

> Collins, J., et al. "ABO: Dataset and Benchmarks for Real-World 3D Object
> Understanding." CVPR 2022.

This is a learning project, not legal advice.
