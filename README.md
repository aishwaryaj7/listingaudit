# ListingAudit

ListingAudit is an AI agent that audits online shop listings against EU consumer and
product-safety rules: honest discounts (the 30-day price rule), manufacturer and safety
information (GPSR), unit prices, textile fibre labels, energy labels, and photos that
match the description. It writes an audit report with evidence, and a human reviewer
approves every finding.

> Status: **step 3 of 10**, the rulebook: a rules checklist and a searchable copy of the
> laws, with citations. The agent itself comes in later steps.

## What's here so far

- A Postgres database (in Docker) holding the full
  [Amazon Berkeley Objects](https://amazon-berkeley-objects.s3.amazonaws.com/index.html)
  catalogue: 147,702 product listings and an index of 398,212 product photos.
- Scripts to download the data, clean it, and load it into the database.
- Product photos for a sample of the categories the rules cover
  (textiles, lamps, groceries, cosmetics, candles, furniture).
- **A synthetic German marketplace** built from 800 real products: prices with 90 days of
  price history, sellers, manufacturers, safety information, unit prices, fibre
  compositions and energy labels. There is a practice set and an exam set of 400 listings
  each, made from different products with different random seeds.
- **Planted rule breaks and a secret answer key.** 220 listings in each set break at least
  one rule (fake discounts, missing manufacturer details, wrong unit prices, swapped
  photos, ...), in an easy and a hard version. About 75 more are traps that look wrong but
  are legal. The answer key lives in its own schema that the agent's database login
  cannot read.
- **A rules checklist** (`src/listingaudit/rulebook/checklist.yaml`): for each rule, what to
  check, what looks wrong but is fine, which listing fields hold the evidence, and which
  article of which law it comes from; and which rules apply to which kind of product.
- **A searchable rulebook** in Postgres: nine law texts (EU regulations and directives,
  the Commission's guidance on the 30-day price rule, a Court of Justice judgment, and the
  German Price Indication Ordinance), cleaned with Beautiful Soup and cut into 542 chunks
  that keep their article and paragraph numbers. It offers keyword search (Postgres
  full-text search ranked with BM25), meaning search (pgvector, with the
  `multilingual-e5-small` embedding model running locally) and hybrid search (Reciprocal
  Rank Fusion of the two). Every result carries a citation such as "GPSR Art. 19" and a
  link to the official text.
- **A search evaluation** on 31 test questions: meaning search finds a right article among
  the first five results for 84% of them, hybrid search for 68%, keyword search for 45%.
- Notebooks exploring the data: `notebooks/01_explore_abo.ipynb`,
  `notebooks/02_explore_market.ipynb` and `notebooks/03_explore_rulebook.ipynb`.
- Tests for the data cleaning, the marketplace generator (including a check that every
  listing breaks exactly the rules the answer key says it does), the law page parsers, the
  chunking, the checklist and the search.

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

# 6. Build the synthetic marketplace and its answer key (a few seconds)
uv run python -m listingaudit.market.build

# 7. Download the law texts (~3 MB) and build the rulebook. The first run also
#    downloads the embedding model (~470 MB) into data/models.
uv run python -m listingaudit.rulebook.build

# 8. Run the tests
uv run pytest

# 9. Measure the search on the test questions
uv run python -m listingaudit.rulebook.evaluate

# 10. Explore the data
uv run jupyter lab notebooks/
```

The marketplace build always gives the same data: the random seeds and the marketplace's
"today" (2 November 2026) are fixed, and `uv.lock` pins the exact versions of NumPy and
Faker that generate it.

On Windows PowerShell, use `copy .env.example .env` in step 1. Everything else is the same.

## Project layout

```
db/schema.sql                             tables for the ABO catalogue
db/market.sql                             tables for the marketplace and the answer key
db/rulebook.sql                           tables for the rulebook
src/listingaudit/config.py                settings (read from .env)
src/listingaudit/db.py                    database connection helpers
src/listingaudit/data/abo.py              cleans one raw ABO record into a flat row
src/listingaudit/data/categories.py       which product types matter for which rule
src/listingaudit/data/download_*.py       download scripts
src/listingaudit/data/load_abo.py         loads the data into Postgres
src/listingaudit/market/catalogue.py      picks the products and reads pack sizes, colours, fibres
src/listingaudit/market/prices.py         prices and 90-day price histories
src/listingaudit/market/companies.py      fictional sellers and manufacturers
src/listingaudit/market/rules.py          the rules, in plain words, with their legal sources
src/listingaudit/market/plan.py           decides which listings get which problems and traps
src/listingaudit/market/listing.py        builds one listing, then breaks rules on purpose
src/listingaudit/market/generate.py       builds one split (practice or exam)
src/listingaudit/market/models.py         the data shapes, checked by Pydantic
src/listingaudit/market/build.py          builds both splits and saves them to Postgres
src/listingaudit/rulebook/checklist.yaml  the rules checklist
src/listingaudit/rulebook/checklist.py    reads and checks the checklist
src/listingaudit/rulebook/sources.py      the nine law texts and where they come from
src/listingaudit/rulebook/download.py     downloads the law texts
src/listingaudit/rulebook/parse.py        turns law pages into clean articles
src/listingaudit/rulebook/chunk.py        cuts articles into chunks with citations
src/listingaudit/rulebook/embed.py        the embedding model
src/listingaudit/rulebook/build.py        builds the rulebook in Postgres
src/listingaudit/rulebook/search.py       keyword, meaning and hybrid search
src/listingaudit/rulebook/questions.yaml  test questions with their right answers
src/listingaudit/rulebook/evaluate.py     scores the search methods on the test questions
notebooks/                                data exploration
tests/                                    tests
data/                                     downloaded files (not in git)
```

## Data and licence

Product data and photos come from the Amazon Berkeley Objects (ABO) dataset by
Amazon.com and UC Berkeley, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

> Collins, J., et al. "ABO: Dataset and Benchmarks for Real-World 3D Object
> Understanding." CVPR 2022.

The law texts come from [EUR-Lex](https://eur-lex.europa.eu) (© European Union; reuse is
authorised by Commission Decision 2011/833/EU) and from
[gesetze-im-internet.de](https://www.gesetze-im-internet.de) (German laws are official works
without copyright, § 5 UrhG). The embedding model is
[intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small)
(MIT licence).

Sellers, manufacturers, prices and addresses in the marketplace are made up. Their email
addresses and websites use the reserved `.example` domain, so none can belong to a real
company.

This is a learning project, not legal advice.
