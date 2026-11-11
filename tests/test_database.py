"""The built marketplace and the permissions of the agent's role.

Needs a running database and a finished build (python -m listingaudit.market.build);
skipped otherwise.
"""

import psycopg
import pytest

from listingaudit.db import connect, connect_as_agent
from listingaudit.market.build import SEEDS
from listingaudit.market.generate import SPLIT_SIZE
from listingaudit.market.prices import HISTORY_DAYS


@pytest.fixture(scope="module")
def owner():
    try:
        conn = connect()
    except psycopg.OperationalError:
        pytest.skip("the database is not running")
    with conn:
        if conn.execute("SELECT to_regclass('market.offers')").fetchone()[0] is None:
            pytest.skip("no marketplace yet: run python -m listingaudit.market.build")
        yield conn


@pytest.fixture
def agent(owner):
    with connect_as_agent() as conn:
        yield conn


def count(conn, query: str) -> int:
    return conn.execute(query).fetchone()[0]


def test_every_listing_is_saved_with_its_answer_and_prices(owner):
    assert count(owner, "SELECT count(*) FROM market.offers") == SPLIT_SIZE * len(SEEDS)
    without_answer = """
        SELECT count(*) FROM market.offers o
        LEFT JOIN answer_key.cases c USING (offer_id)
        WHERE c.offer_id IS NULL
    """
    assert count(owner, without_answer) == 0
    wrong_length = f"""
        SELECT count(*) FROM (
            SELECT offer_id FROM market.price_history
            GROUP BY offer_id HAVING count(*) <> {HISTORY_DAYS}
        ) AS histories
    """
    assert count(owner, wrong_length) == 0


def test_the_agent_can_read_the_marketplace(agent):
    assert count(agent, "SELECT count(*) FROM market.offers") == SPLIT_SIZE * len(SEEDS)
    assert count(agent, "SELECT count(*) FROM market.price_history") > 0


@pytest.mark.parametrize(
    "table", ["answer_key.cases", "answer_key.problems", "answer_key.traps", "public.listings"]
)
def test_the_agent_cannot_read_the_answer_key_or_the_raw_data(agent, table):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        agent.execute(f"SELECT * FROM {table} LIMIT 1")


def test_the_agent_cannot_change_the_marketplace(agent):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        agent.execute("UPDATE market.offers SET price = 0.01")
