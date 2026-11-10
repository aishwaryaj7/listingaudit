"""Build the pretend marketplace and its answer key, and save both to Postgres.

    uv run python -m listingaudit.market.build

Builds a practice set and an exam set of 400 listings each from the products
downloaded in step 1. Running it again rebuilds both from scratch, and because
the seeds and the snapshot date are fixed, the data comes out exactly the same.
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
# "Today" on the pretend marketplace. Fixed, so the data doesn't change with the date.
SNAPSHOT_DATE = date(2026, 11, 2)
# One seed per split. The exam set gets products the practice set doesn't use.
SEEDS = {"practice": 42, "exam": 4242}

# Where each part of a split goes, in an order that respects the foreign keys
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
    """Generate every split, so that no two splits show the same product or photo.

    Every split chooses its products first. Only then are photos swapped in, and
    only photos of products that no split sells. (Otherwise the practice set
    could use up, as a photo, a candle the exam set needs to sell.)
    """
    chosen: dict[str, list[Product]] = {}
    shown: set[str] = set()  # products on sale, or whose photo is shown
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
    """Write rows with COPY, the fastest way to put many rows into Postgres."""
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
    """Give the agent a login that can read the marketplace and nothing else."""
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
    lines = [f"Built the pretend marketplace as of {SNAPSHOT_DATE}:"]
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
            raise SystemExit("No products found. Run the step 1 scripts first (see README).")
        results = build_splits(products, photos)
        save(cur, results)
    print(summary(results))


if __name__ == "__main__":
    main()
