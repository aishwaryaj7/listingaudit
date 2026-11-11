"""Build the synthetic marketplace and its answer key in Postgres.

    uv run python -m listingaudit.market.build

Generates a practice split and an exam split of 400 listings each from the ABO
catalogue. Seeds and the snapshot date are fixed, so every rebuild produces
the same data.
"""

from collections import Counter
from datetime import date

from psycopg import Cursor, sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from listingaudit.config import PROJECT_ROOT, settings
from listingaudit.db import connect
from listingaudit.market.catalogue import Photo, Product, load_catalogue
from listingaudit.market.generate import SplitResult, choose_products, generate_split
from listingaudit.market.models import Case, Offer, PricePoint, Problem, Seller, Trap

MARKET_SCHEMA_FILE = PROJECT_ROOT / "db" / "market.sql"
# Fixed "today" for the marketplace, so the data doesn't depend on the run date
SNAPSHOT_DATE = date(2026, 11, 2)
# One seed per split; later splits skip products used by earlier ones
SEEDS = {"practice": 42, "exam": 4242}

# Destination table for each part of a split, in foreign-key order
TABLES: list[tuple[str, type[BaseModel], str]] = [
    ("market.sellers", Seller, "sellers"),
    ("market.offers", Offer, "offers"),
    ("market.price_history", PricePoint, "prices"),
    ("answer_key.cases", Case, "cases"),
    ("answer_key.problems", Problem, "problems"),
    ("answer_key.traps", Trap, "traps"),
]


def build_splits(
    catalogue: list[Product], photos: list[Photo], seeds: dict[str, int] = SEEDS
) -> dict[str, SplitResult]:
    """Generate all splits so that no product or photo appears in more than one.

    Products are chosen for every split before any photos are swapped in, and only
    photos of unsold products are used. Otherwise one split could use up, as a
    swap photo, a product that another split needs to sell.
    """
    chosen: dict[str, list[Product]] = {}
    shown: set[str] = set()  # item_ids already used, as a listing or as a photo
    for split, seed in seeds.items():
        chosen[split] = choose_products(catalogue, seed, exclude=shown)
        shown |= {p.item_id for p in chosen[split]}

    results: dict[str, SplitResult] = {}
    for split, seed in seeds.items():
        free_photos = [photo for photo in photos if photo.item_id not in shown]
        first_seller_id = 1 + sum(len(r.sellers) for r in results.values())
        results[split] = generate_split(
            chosen[split], free_photos, split, seed, SNAPSHOT_DATE, first_seller_id
        )
        shown |= results[split].swapped_photo_items
    return results


def copy_rows(cur: Cursor, table: str, model: type[BaseModel], rows: list[BaseModel]) -> None:
    """Bulk-load rows with COPY."""
    columns = list(model.model_fields)
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(*table.split(".")), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    with cur.copy(statement) as copy:
        for row in rows:
            # JSON mode turns Decimals and dates into text and rules into their names
            values = row.model_dump(mode="json")
            copy.write_row([_adapt(values[column]) for column in columns])


def _adapt(value):
    return Jsonb(value) if isinstance(value, dict) else value


def grant_agent_access(cur: Cursor) -> None:
    """Create or update the agent's login role, with read access to the market schema."""
    agent = sql.Identifier(settings.agent_db_user)
    password = sql.Literal(settings.agent_db_password)
    exists = cur.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", [settings.agent_db_user]
    ).fetchone()
    if exists:
        cur.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(agent, password))
    else:
        cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(agent, password))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA market TO {}").format(agent))
    cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA market TO {}").format(agent))
    # Nothing on answer_key: a new schema gives other roles no access at all


def save(cur: Cursor, results: dict[str, SplitResult]) -> None:
    # Both schemas are rebuilt from scratch, which also picks up schema changes
    cur.execute("DROP SCHEMA IF EXISTS answer_key, market CASCADE")
    cur.execute(MARKET_SCHEMA_FILE.read_text())
    cur.execute("INSERT INTO market.snapshot (snapshot_date) VALUES (%s)", [SNAPSHOT_DATE])
    for table, model, part in TABLES:
        for result in results.values():
            copy_rows(cur, table, model, getattr(result, part))
    grant_agent_access(cur)


def summary(results: dict[str, SplitResult]) -> str:
    lines = [f"Built the synthetic marketplace as of {SNAPSHOT_DATE}:"]
    for split, r in results.items():
        kinds = Counter(case.kind for case in r.cases)
        lines.append(
            f"  {split:9} {len(r.offers)} listings ({kinds['problem']} with problems, "
            f"{kinds['tricky']} tricky, {kinds['clean']} clean), {len(r.problems)} planted "
            f"problems, {len(r.sellers)} sellers, {len(r.prices):,} daily prices"
        )
    lines.append(
        f"The agent logs in as '{settings.agent_db_user}' and can read the market schema only."
    )
    return "\n".join(lines)


def main() -> None:
    # One transaction: if anything fails, the database keeps its old data
    with connect() as conn, conn.cursor() as cur:
        products, photos = load_catalogue(conn)
        if not products:
            raise SystemExit("No products found. Load the ABO catalogue first (see README).")
        results = build_splits(products, photos)
        save(cur, results)
    print(summary(results))


if __name__ == "__main__":
    main()
