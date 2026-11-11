"""Rulebook search, and what the agent's role may do with it.

The tests under "With the database" need a running database and a finished build
(python -m listingaudit.rulebook.build); they are skipped otherwise.
"""

import psycopg
import pytest

from listingaudit.db import connect, connect_as_agent
from listingaudit.rulebook.checklist import load_checklist
from listingaudit.rulebook.search import keyword_search, reciprocal_rank_fusion, search


def test_rrf_rewards_being_high_in_both_lists():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]])
    assert [item for item, _ in fused] == ["b", "a", "c"]
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)


def test_rrf_keeps_items_found_by_only_one_method():
    fused = dict(reciprocal_rank_fusion([["a"], ["b", "a"]]))
    assert set(fused) == {"a", "b"}
    assert fused["a"] > fused["b"]


# --- With the database ---------------------------------------------------------


@pytest.fixture(scope="module")
def owner():
    try:
        conn = connect()
    except psycopg.OperationalError:
        pytest.skip("the database is not running")
    with conn:
        if conn.execute("SELECT to_regclass('rulebook.chunks')").fetchone()[0] is None:
            pytest.skip("no rulebook yet: run python -m listingaudit.rulebook.build")
        yield conn


@pytest.fixture
def agent(owner):
    with connect_as_agent() as conn:
        yield conn


def test_every_law_has_chunks_with_citations(owner):
    rows = owner.execute(
        "SELECT l.law_id, count(c.chunk_id) FROM rulebook.laws l "
        "LEFT JOIN rulebook.chunks c USING (law_id) GROUP BY l.law_id"
    ).fetchall()
    assert rows and all(count > 0 for _, count in rows)
    empty = owner.execute(
        "SELECT count(*) FROM rulebook.chunks WHERE citation = '' OR text = '' OR url = ''"
    ).fetchone()[0]
    assert empty == 0


def test_checklist_articles_are_in_the_rulebook(owner):
    """Every article the checklist tells the agent to quote must exist."""
    chunks = owner.execute(
        "SELECT law_id, article, paragraph, text FROM rulebook.chunks"
    ).fetchall()
    for rule, spec in load_checklist().rules.items():
        for ref in spec.law:
            article = [c for c in chunks if c[0] == ref.law and c[1] == ref.article]
            assert article, f"{rule}: {ref.law} {ref.article} is not in the rulebook"
            if ref.paragraph:
                # Its own chunk, or a chunk of several paragraphs that contains it
                assert any(
                    c[2] == ref.paragraph or _starts_paragraph(c[3], ref.paragraph) for c in article
                ), f"{rule}: {ref.law} {ref.article}({ref.paragraph}) not found"
            if ref.point:
                assert any(f"({ref.point})" in c[3] for c in article), (
                    f"{rule}: point ({ref.point}) not in {ref.law} {ref.article}"
                )


def _starts_paragraph(text: str, label: str) -> bool:
    return any(line.startswith((f"{label}.", f"({label})")) for line in text.split("\n"))


def test_the_agent_can_read_but_not_change_the_rulebook(agent):
    assert agent.execute("SELECT count(*) FROM rulebook.chunks").fetchone()[0] > 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        agent.execute("DELETE FROM rulebook.chunks")


def test_keyword_search_finds_exact_terms(owner):
    [hit] = keyword_search(owner, "Grundpreis Fertigpackungen", k=1)
    assert hit.citation.startswith("PAngV § 4")
    [hit] = keyword_search(owner, "cashgora", k=1)  # a rare fibre name
    assert hit.citation == "Regulation (EU) No 1007/2011 Annex I"


def test_meaning_search_crosses_languages(owner):
    """A German question finds the English article that answers it."""
    question = "Welche Angaben zum Hersteller muss ein Online-Angebot enthalten?"
    [hit] = search(owner, question, "meaning", k=1, laws=["gpsr"])
    assert hit.citation == "GPSR Art. 19"


def test_hybrid_search_merges_both_rankings(owner):
    hits = search(owner, "Must a product listing include a picture of the product?", "hybrid")
    assert len(hits) == 5 and len({hit.chunk_id for hit in hits}) == 5
    assert any(hit.citation == "GPSR Art. 19" for hit in hits)


def test_search_can_be_limited_to_some_laws(owner):
    hits = search(owner, "information about the manufacturer", "hybrid", k=5, laws=["gpsr"])
    assert hits and all(hit.chunk_id.startswith("gpsr/") for hit in hits)
