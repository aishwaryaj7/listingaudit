# ListingAudit

ListingAudit is an AI agent that audits online shop listings against EU consumer and
product-safety rules: honest discounts (the 30-day price rule), manufacturer and safety
information (GPSR), unit prices, textile fibre labels, energy labels, and photos that
match the description. It writes an audit report with evidence, and a human reviewer
approves every finding.

> Status: **step 2 of 10**, a pretend marketplace with planted rule breaks. The agent
> itself comes in later steps.

## What's here so far

- A Postgres database (in Docker) holding the full
  [Amazon Berkeley Objects](https://amazon-berkeley-objects.s3.amazonaws.com/index.html)
  catalogue: 147,702 product listings and an index of 398,212 product photos.
- Scripts to download the data, clean it, and load it into the database.
- Product photos for a sample of the categories our rules care about
  (textiles, lamps, groceries, cosmetics, candles, furniture).
- **A pretend German marketplace** built from 800 real products: prices with 90 days of
  price history, sellers, manufacturers, safety information, unit prices, fibre
  compositions and energy labels. There is a practice set and an exam set of 400 listings
  each, made from different products with different random seeds.
- **Planted rule breaks and a secret answer key.** 220 listings in each set break at least
  one rule (fake discounts, missing manufacturer details, wrong unit prices, swapped
  photos, ...), in an easy and a hard version. About 75 more are traps that look wrong but
  are legal. The answer key lives in its own schema that the agent's database login
  cannot read.
- Notebooks exploring the data: `notebooks/01_explore_abo.ipynb` and
  `notebooks/02_explore_market.ipynb`.
- Tests for the data cleaning and the marketplace generator, including a check that every
  listing breaks exactly the rules the answer key says it does.

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

# 6. Build the pretend marketplace and its answer key (a few seconds)
uv run python -m listingaudit.market.build

# 7. Run the tests
uv run pytest

# 8. Explore the data
uv run jupyter lab notebooks/
```

The marketplace build always gives the same data: the random seeds and the marketplace's
"today" (2 November 2026) are fixed, and `uv.lock` pins the exact versions of NumPy and
Faker that generate it.

On Windows PowerShell, use `copy .env.example .env` in step 1. Everything else is the same.

## Project layout

```
db/schema.sql                         tables for the ABO catalogue (step 1)
db/market.sql                         tables for the marketplace and the answer key (step 2)
src/listingaudit/config.py            settings (read from .env)
src/listingaudit/db.py                database connection helpers
src/listingaudit/data/abo.py          cleans one raw ABO record into a flat row
src/listingaudit/data/categories.py   which product types matter for which rule
src/listingaudit/data/download_*.py   download scripts
src/listingaudit/data/load_abo.py     loads the data into Postgres
src/listingaudit/market/catalogue.py  picks the products and reads pack sizes, colours, fibres
src/listingaudit/market/prices.py     prices and 90-day price histories
src/listingaudit/market/companies.py  fictional sellers and manufacturers
src/listingaudit/market/rules.py      the rules, in plain words, with their legal sources
src/listingaudit/market/plan.py       decides which listings get which problems and traps
src/listingaudit/market/listing.py    builds one listing, then breaks rules on purpose
src/listingaudit/market/generate.py   builds one split (practice or exam)
src/listingaudit/market/models.py     the data shapes, checked by Pydantic
src/listingaudit/market/build.py      builds both splits and saves them to Postgres
notebooks/                            data exploration
tests/                                tests
data/                                 downloaded files (not in git)
```

## Data and licence

Product data and photos come from the Amazon Berkeley Objects (ABO) dataset by
Amazon.com and UC Berkeley, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

> Collins, J., et al. "ABO: Dataset and Benchmarks for Real-World 3D Object
> Understanding." CVPR 2022.

Sellers, manufacturers, prices and addresses in the marketplace are made up. Their email
addresses and websites use the reserved `.example` domain, so none can belong to a real
company.

This is a learning project, not legal advice.
