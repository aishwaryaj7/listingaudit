# ListingAudit

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

An AI agent that audits online shop listings against EU consumer and product-safety rules,
such as honest discounts, manufacturer and safety information, unit prices and product
labels. It cites the law behind each possible problem, and a person approves every finding
before anything reaches the seller.

Built with Python, LangGraph, MCP, Azure OpenAI, CLIP, PostgreSQL with pgvector, FastAPI
and Streamlit.

**Demo video:** https://youtu.be/PIF6hISSNfA

## Setup

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) and
[uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/aishwaryaj7/listingaudit.git
cd listingaudit
cp .env.example .env
uv sync
docker compose up -d
```

Load the data and run the tests:

```bash
uv run python -m listingaudit.data.download_abo
uv run python -m listingaudit.data.load_abo
uv run python -m listingaudit.data.download_images --per-type 100
uv run python -m listingaudit.market.build
uv run python -m listingaudit.rulebook.build
uv run pytest
```

The agent needs a language model. Deploy gpt-5-mini on Azure OpenAI and set
`VISION_BASE_URL`, `VISION_API_KEY` and `VISION_MODEL` in `.env`.

## Usage

Audit a listing in the terminal:

```bash
uv run python -m listingaudit.agent.run P0171
```

Or start the review API, then the review screen in a second terminal, and open
http://localhost:8501:

```bash
uv run uvicorn listingaudit.review.api:app
uv run streamlit run src/listingaudit/review/screen.py
```

## Data and licence

Product data and photos come from the
[Amazon Berkeley Objects](https://amazon-berkeley-objects.s3.amazonaws.com/index.html)
dataset by Amazon.com and UC Berkeley, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Law texts come from
[EUR-Lex](https://eur-lex.europa.eu) (© European Union) and
[gesetze-im-internet.de](https://www.gesetze-im-internet.de). Sellers, manufacturers and
prices in the test marketplace are made up. This is a learning project, not legal advice.
